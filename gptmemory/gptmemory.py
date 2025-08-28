import json
import logging
import asyncio
import aiohttp
import discord
from io import BytesIO
from datetime import datetime
from difflib import get_close_matches
from typing import Optional, Union, List, Dict, Any
from dataclasses import dataclass
from expiringdict import ExpiringDict
from openai import AsyncOpenAI, NotGiven
from openai.types.chat import ChatCompletionMessageFunctionToolCall
from tiktoken import encoding_for_model
from redbot.core import commands
from redbot.core.bot import Red

import gptmemory.functions.base
from gptmemory.commands import GptMemoryBase
from gptmemory.utils import sanitize, make_image_content, process_image, get_text_contents, chunk_and_send
from gptmemory.schema import MemoryChangeList
from gptmemory.constants import URL_PATTERN, RESPONSE_CLEANUP_PATTERN, DISCORD_MESSAGE_LINK_PATTERN, IMAGE_EXTENSIONS

log = logging.getLogger("gptmemory")

GptImageContent = List[Dict[str, str]]
GptMessage = Dict[str, Union[str, GptImageContent]]


@dataclass
class GptMemoryResult:
    messages: int = 0
    images: int = 0
    tokens_backread: int = 0
    tokens_recaller: int = 0
    tokens_system: int = 0
    tokens_responder: int = 0
    tokens_after_tools: int = 0
    tokens_memorizer: int = 0


class GptMemory(GptMemoryBase):
    """OpenAI-powered user with persistent memory and various tools."""

    def __init__(self, bot: Red):
        super().__init__(bot)
        self.openai_client: Optional[AsyncOpenAI] = None
        self.image_cache: Dict[int, GptImageContent] = ExpiringDict(max_len=50, max_age_seconds=24*60*60)
        self.available_function_calls = set(gptmemory.functions.base.get_all_function_calls())
        all_function_names = [tool.schema.function.name for tool in self.available_function_calls]
        log.info(f"{all_function_names=}")


    async def cog_load(self):
        await self.initialize_function_calls()
        await self.initialize_openai_client()
        all_config = await self.config.all_guilds()
        for guild_id, config in all_config.items():
            self.memory[guild_id] = config["memory"]


    async def cog_unload(self):
        if self.openai_client:
            await self.openai_client.close()


    async def initialize_function_calls(self):
        all_function_calls = gptmemory.functions.base.get_all_function_calls()
        self.available_function_calls = set(all_function_calls)
        for function in all_function_calls:
            for api in function.apis:
                secret = (await self.bot.get_shared_api_tokens(api[0])).get(api[1])
                if not secret:
                    self.available_function_calls.discard(function)


    async def initialize_openai_client(self):
        api_key = (await self.bot.get_shared_api_tokens("openai")).get("api_key")
        if not api_key:
            return
        self.openai_client = AsyncOpenAI(api_key=api_key)


    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name, _):
        await self.initialize_function_calls()
        if service_name == "openai":
            await self.initialize_openai_client()


    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        ctx: commands.Context = await self.bot.get_context(message) 
        if not await self.is_valid_trigger(ctx):
            return
        
        await ctx.channel.typing()
        if match := URL_PATTERN.search(message.content):
            if f"<{match.group(0)}>" not in message.content: # non-embedding links
                ctx = await self.wait_for_embed(ctx)

        await self.run_response(ctx)

    
    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        if before.name == after.name:
            return
        for guild in self.bot.guilds:
            if before.name in self.memory.get(guild.id, {}):
                self.memory[guild.id][after.name] = self.memory[guild.id][before.name]
                del self.memory[guild.id][before.name]
                async with self.config.guild(guild).memory() as memory:
                    if before.name in memory:
                        memory[after.name] = memory[before.name]
                        del memory[before.name]
                log.info(f"Moved user memory {before.name=} {after.name=}")


    async def is_valid_trigger(self, ctx: commands.Context) -> bool:
        if self.bot.user not in ctx.message.mentions:
            return False
        if ctx.author.bot:
            return False
        if not ctx.guild:
            return False

        channel_mode = await self.config.guild(ctx.guild).channel_mode()
        channel_list = await self.config.guild(ctx.guild).channels()
        if channel_mode == "blacklist" and ctx.channel.id in channel_list:
            return False
        elif channel_mode == "whitelist" and ctx.channel.id not in channel_list:
            return False

        if await self.bot.cog_disabled_in_guild(self, ctx.guild):
            return False
        if not await self.bot.ignored_channel_or_guild(ctx):
            return False
        if not await self.bot.allowed_by_whitelist_blacklist(ctx.author):
            return False

        if not self.openai_client:
            await self.initialize_openai_client()
        if not self.openai_client:
            return False

        return True


    @staticmethod
    async def wait_for_embed(ctx: commands.Context) -> commands.Context:
        for n in range(2):
            if ctx.message.embeds:
                return ctx
            await asyncio.sleep(1)
            ctx.message = await ctx.channel.fetch_message(ctx.message.id)
        return ctx        


    async def run_response(self, ctx: commands.Context):
        assert ctx.guild
        if ctx.guild.id not in self.memory:
            self.memory[ctx.guild.id] = {}
        memories = list(self.memory[ctx.guild.id].keys())

        result = GptMemoryResult()
        messages = await self.get_message_history(ctx, result)
        recalled_memories = await self.execute_recaller(ctx, messages, memories, result)
        await self.execute_responder_and_memorizer(ctx, messages, memories, recalled_memories, result)
        log.info(result)


    async def execute_recaller(self,
                               ctx: commands.Context,
                               messages: List[GptMessage],
                               memories: List[str],
                               result: GptMemoryResult
                               ) -> Dict[str, str]:
        """
        Runs an openai completion with the chat history and a list of memories from the database
        and returns a dictionary of memories and their contents as chosen by the LLM.
        """
        assert ctx.guild and self.openai_client
        if not memories:
            return {}

        temp_messages = get_text_contents(messages)
        temp_memories = list(memories)
        memories_to_recall = set()
        for memory in memories:
            if any(f"[Username: {memory}]" in msg["content"] for msg in temp_messages):
                temp_memories.remove(memory)
                memories_to_recall.add(memory)

        temp_memories_str = ", ".join(temp_memories)
        system_content = (await self.config.guild(ctx.guild).prompt_recaller()).format(temp_memories_str)
        system_prompt = {
            "role": "system",
            "content": system_content
        }
        temp_messages.insert(0, system_prompt)

        model = await self.config.guild(ctx.guild).model_recaller()
        response = await self.openai_client.beta.chat.completions.create(
            model=model,
            messages=temp_messages,
            reasoning_effort=await self.config.guild(ctx.guild).effort_recaller() if "gpt-5" in model else NotGiven()
        )
        completion = response.choices[0].message.content
        if completion:
            memories_to_recall.update([memory for memory in temp_memories if memory.lower() in completion.lower()])
        if response.usage:
            result.tokens_recaller = response.usage.completion_tokens
        log.info(f"{memories_to_recall=}")

        recalled_memories = {k: v for k, v in self.memory[ctx.guild.id].items() if k in memories_to_recall}
        return recalled_memories or {}


    async def execute_responder_and_memorizer(self,
                                              ctx: commands.Context,
                                              messages: List[GptMessage],
                                              memories: List[str],
                                              recalled_memories: Dict[str, str],
                                              result: GptMemoryResult
                                              ) -> None:
        task_results = await asyncio.gather(
            self.execute_responder(ctx, messages, recalled_memories, result),
            self.execute_memorizer(ctx, messages, memories, recalled_memories, result),
            return_exceptions=True
        )
        for idx, res in enumerate(task_results):
            if isinstance(res, BaseException):
                log.error(f"Error in {'memorizer' if idx else 'responder'}: {type(res).__name__}", res)


    async def execute_responder(self,
                                ctx: commands.Context,
                                messages: List[GptMessage],
                                recalled_memories: Dict[str, str],
                                result: GptMemoryResult
                                ) -> GptMessage:
        """
        Runs an openai completion with the chat history and the contents of memories
        and returns a response message after sending it to the user.
        """
        assert ctx.guild and self.bot.user and self.openai_client and isinstance(ctx.channel, discord.TextChannel)
        
        model = await self.config.guild(ctx.guild).model_responder()
        recalled_memories_str = "\n".join(f"[Memory of {k}:] {v}" for k, v in recalled_memories.items())
        system_content = (await self.config.guild(ctx.guild).prompt_responder()).format(
            botname=self.bot.user.name,
            servername=ctx.guild.name,
            channelname=ctx.channel.name,
            emotes=(await self.config.guild(ctx.guild).emotes()) or "[None]",
            currentdatetime=datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z%z"),
            memories=recalled_memories_str,
        )
        encoding = encoding_for_model("gpt-4o")
        result.tokens_system = len(encoding.encode(system_content))
        
        system_prompt = {
            "role": "developer" if "gpt-5" in model else "system",
            "content": system_content
        }
        temp_messages = [msg for msg in messages]
        temp_messages.insert(0, system_prompt)

        tools = [t for t in self.available_function_calls
            if t.schema.function.name not in await self.config.guild(ctx.guild).disabled_functions()]

        async with ctx.channel.typing():
            response = await self.openai_client.chat.completions.create(
                model=model,
                messages=temp_messages, # type: ignore
                max_tokens=NotGiven() if "gpt-5" in model else await self.config.guild(ctx.guild).response_tokens(),
                max_completion_tokens=await self.config.guild(ctx.guild).response_tokens() if "gpt-5" in model else NotGiven(),
                tools=[t.asdict() for t in tools], # type: ignore
            )
            if response.usage:
                result.tokens_responder = response.usage.completion_tokens

            if response.choices[0].message.tool_calls:
                temp_messages.append(response.choices[0].message) # type: ignore
                max_tool_length = await self.config.guild(ctx.guild).max_tool()
                for call in response.choices[0].message.tool_calls:
                    assert isinstance(call, ChatCompletionMessageFunctionToolCall)
                    try:
                        cls = next(t for t in tools if t.schema.function.name == call.function.name)
                        args = json.loads(call.function.arguments)
                        tool_result = await cls(ctx, self).run(**args)
                    except Exception:  # tools should handle specific errors internally, but broad errors should not stop the responder
                        tool_result = "[Error]"
                        log.exception("Calling tool")

                    tool_result = tool_result.strip()
                    if len(tool_result) > max_tool_length:
                        tool_result = tool_result[:max_tool_length-3] + "..."
                    log.info(f"{call.function.arguments=}")
                    log.info(f"{tool_result=}")

                    temp_messages.append({
                        "role": "tool",
                        "content": tool_result,
                        "tool_call_id": call.id,
                    })

                model = await self.config.guild(ctx.guild).model_responder()
                response = await self.openai_client.chat.completions.create(
                    model=model,
                    messages=temp_messages, # type: ignore
                    max_tokens=NotGiven() if "gpt-5" in model else await self.config.guild(ctx.guild).response_tokens(),
                    max_completion_tokens=await self.config.guild(ctx.guild).response_tokens() if "gpt-5" in model else NotGiven(),
                    reasoning_effort=await self.config.guild(ctx.guild).effort_responder() if "gpt-5" in model else NotGiven()
                )
                if response.usage:
                    result.tokens_after_tools = response.usage.completion_tokens

            completion = response.choices[0].message.content
            if completion:
                log.info(f"{completion=}")
                reply_content = RESPONSE_CLEANUP_PATTERN.sub("", completion)
                await chunk_and_send(ctx, reply_content)

        response_message = {
            "role": "assistant",
            "content": reply_content
        }
        return response_message


    async def execute_memorizer(self,
                                ctx: commands.Context,
                                messages: List[GptMessage],
                                memories: List[str],
                                recalled_memories: Dict[str, str],
                                result: GptMemoryResult
                                ) -> None:
        """
        Runs an openai completion with the chat history, a list of memories, and the contents of some memories,
        and executes database operations as decided by the LLM.
        """
        assert ctx.guild and self.openai_client
        if not await self.config.guild(ctx.guild).allow_memorizer():
            return

        if await self.config.guild(ctx.guild).memorizer_user_only():
            memories = [memory for memory in memories if any(member.name == memory for member in ctx.guild.members)]
        memories_str = ", ".join(memories)
        recalled_memories_str = "\n".join(f"[Memory of {k}:] {v}" for k, v in recalled_memories.items() if k in memories)
        system_content = (await self.config.guild(ctx.guild).prompt_memorizer()).format(
            memories_str,
            recalled_memories_str,
            botname=ctx.me.name
        )
        system_prompt = {
            "role": "system",
            "content": system_content
        }
        temp_messages = get_text_contents(messages)
        num_backread = await self.config.guild(ctx.guild).backread_memorizer()
        if len(temp_messages) > num_backread:
            temp_messages = temp_messages[-num_backread:]
        temp_messages.insert(0, system_prompt)

        model = await self.config.guild(ctx.guild).model_memorizer()
        response = await self.openai_client.beta.chat.completions.parse(
            model=model,
            messages=temp_messages,
            response_format=MemoryChangeList,
            reasoning_effort=await self.config.guild(ctx.guild).effort_memorizer() if "gpt-5" in model else NotGiven()
        )
        completion = response.choices[0].message
        if response.usage:
            result.tokens_memorizer = response.usage.completion_tokens
        if completion.refusal:
            log.warning(completion.refusal)
            return
        if not completion.parsed or not completion.parsed.memory_changes:
            return

        memory_changes = []
        async with self.config.guild(ctx.guild).memory() as memory:
            for change in completion.parsed.memory_changes:
                action, name, content = change.action_type, change.memory_name, change.memory_content

                if name not in memory and action != "create":
                    matches = get_close_matches(name, memory)
                    if not matches:
                        continue
                    name = matches[0]

                if action == "delete":
                    del memory[name]
                    del self.memory[ctx.guild.id][name]
                    log.info(f"delete memory / {name=}")

                elif action == "create" and name not in memory:
                    memory[name] = content
                    self.memory[ctx.guild.id][name] = content
                    log.info(f"create memory / {name=} / {content=}")

                elif action == "modify" and name in memory:
                    memory[name] = content
                    self.memory[ctx.guild.id][name] = content
                    log.info(f"modify memory / {name=} / {content=}")

                else:
                    memory[name] += " ... " + content
                    self.memory[ctx.guild.id][name] += " ... " + content
                    log.info(f"append memory / {name=} / {content=}")

                memory_changes.append(name)

        if memory_changes and await self.config.guild(ctx.guild).memorizer_alerts():
            await ctx.send(f"-# Revised memories: {', '.join(memory_changes)}")


    async def get_message_history(self, ctx: commands.Context, result: GptMemoryResult) -> List[GptMessage]:
        assert ctx.guild and self.bot.user
        backread = [message async for message in ctx.channel.history(
            limit=await self.config.guild(ctx.guild).backread_messages(),
            before=ctx.message,
            oldest_first=False
        )]
        backread.insert(0, ctx.message)

        messages = []
        processed_image_sources = []
        tokens = 0
        total_images = 0
        encoding = encoding_for_model("gpt-4o")  # same for gpt-4.1 and their variants

        max_image_size = await self.config.guild(ctx.guild).max_image_resolution()
        max_images = await self.config.guild(ctx.guild).max_images()
        max_quote_length = await self.config.guild(ctx.guild).max_quote()
        max_file_length = await self.config.guild(ctx.guild).max_text_file()
        max_backread_tokens = await self.config.guild(ctx.guild).backread_tokens()
        for n, backmsg in enumerate(backread):
            try:
                quote = backmsg.reference.cached_message or await backmsg.channel.fetch_message(backmsg.reference.message_id) # type: ignore
                # This would prevent chaining message quotes that are already consecutive
                # if len(backread) > n+1 and quote == backread[n+1]:
                #    quote = None
            except (AttributeError, discord.DiscordException):
                quote = None

            images_left = max_images - total_images
            if images_left > 0:
                image_contents = await self.extract_images(backmsg, quote, processed_image_sources, images_left, max_image_size)
                total_images += len(image_contents)
            else:
                image_contents = []
            trim_quote = quote is not None and quote in backread
            text_content = await self.parse_discord_message(backmsg, quote, trim_quote, True, max_quote_length, max_file_length)
            if image_contents:
                image_contents.insert(0, {"type": "text", "text": text_content})
                messages.append({
                    "role": "user",
                    "content": image_contents
                })
            else:
                messages.append({
                    "role": "assistant" if backmsg.author.id == self.bot.user.id else "user",
                    "content": text_content
                })

            text_tokens = len(encoding.encode(text_content))
            image_tokens = 425 * max(0, len(image_contents) - 1)
            tokens += text_tokens + image_tokens
            if n > 0 and tokens > max_backread_tokens:
                break
        
        image_sources = [att.url if isinstance(att, discord.Attachment) else att for att in processed_image_sources]
        log.info(f"{image_sources=}")
        result.tokens_backread = tokens
        result.images = total_images
        result.messages = len(messages)

        return list(reversed(messages))


    async def extract_images(self,
                             message: discord.Message,
                             quote: Optional[discord.Message],
                             processed_sources: List[Union[str, discord.Attachment]],
                             max_images: int,
                             max_image_size: int,
                            ) -> GptImageContent:
        
        if message.id in self.image_cache:
            return self.image_cache[message.id]

        image_contents = []

        # Attachments
        if message.attachments or quote and quote.attachments:
            attachments = enumerate((message.attachments or []) + (quote.attachments if quote and quote.attachments else []))
            images = [(i, att) for i, att in attachments if att.content_type and att.content_type.startswith('image/')]

            for i, image in images[:max_images]:
                if image in processed_sources:
                    continue
                processed_sources.append(image)
                
                fp_before = BytesIO()
                imagescanner: Optional[commands.Cog] = self.bot.get_cog("ImageScanner")
                if imagescanner and message.id in imagescanner.image_cache: # type: ignore
                    _, image_bytes = self.image_cache.get(message.id, ({}, {}))
                    if i in image_bytes:
                        fp_before = BytesIO(image_bytes[i]) # type: ignore
                if fp_before.getbuffer().nbytes == 0:
                    try:
                        await image.save(fp_before, seek_begin=True)
                    except discord.DiscordException:
                        log.warning("Processing image attachments", exc_info=True)
                        continue

                fp_after = process_image(fp_before, max_image_size)
                del fp_before
                if not fp_after:
                    continue

                image_contents.append(make_image_content(fp_after))
                del fp_after

        if image_contents:
            self.image_cache[message.id] = [cnt for cnt in image_contents]
            return image_contents

        # URLs
        image_url = []

        if message.embeds and message.embeds[0].image and message.embeds[0].image.url:
            image_url.append(message.embeds[0].image.url)
        if message.embeds and message.embeds[0].thumbnail and message.embeds[0].thumbnail.url:
            image_url.append(message.embeds[0].thumbnail.url)

        matches = URL_PATTERN.findall(message.content)
        for match in matches:
            if match.endswith(IMAGE_EXTENSIONS):
                image_url.append(match)

        if not image_url:
            return image_contents

        async with aiohttp.ClientSession() as session:
            for url in image_url[:max_images]:
                if url in processed_sources:
                    continue
                processed_sources.append(url)
                
                try:
                    async with session.get(url) as response:
                        response.raise_for_status()
                        fp_before = BytesIO(await response.read())
                except aiohttp.ClientError:
                    log.warning("Processing image URL", exc_info=True)
                    continue
                fp_after = process_image(fp_before, max_image_size)
                del fp_before
                if not fp_after:
                    continue
                image_contents.append(make_image_content(fp_after))
                del fp_after

        if image_contents:
            self.image_cache[message.id] = [cnt for cnt in image_contents]

        return image_contents


    async def parse_discord_message(self,
                                    message: discord.Message,
                                    quote: Optional[discord.Message],
                                    trim_quote: bool,
                                    recursive: bool,
                                    max_quote_length: int,
                                    max_file_length: int,
                                    ) -> str:
        assert message.guild

        content = f"[Username: {sanitize(message.author.name)}]"
        if isinstance(message.author, discord.Member) and message.author.nick:
            content += f" [Alias: {sanitize(message.author.nick)}]"
        starting_len = len(content)

        if message.is_system():
            if message.type == discord.MessageType.new_member:
                content += " [Joined the server]"
            else:
                content += f" {message.system_content}"
        elif message.content:
            content += f" [said:] {message.content}"

        is_generated_image = False
        if message.attachments and len(message.attachments) == 1:
            imagescanner: Optional[commands.Cog] = self.bot.get_cog("ImageScanner")
            metadata: dict[str, Any] = await imagescanner.grab_metadata_dict(message) # type: ignore
            if metadata and metadata.get("Prompt", None):
                is_generated_image = True
                if message.author == message.guild.me:
                    content += f" [[ [Generated image filename: {message.attachments[0].filename}] [Generated image prompt:] {metadata['Prompt']} ]]"
                else:
                    content += f" [[ [Image with prompt:] {metadata['Prompt']} ]]"
        
        if not is_generated_image:
            for attachment in message.attachments:
                content += f" [Attachment: {attachment.filename}]"
        
        for sticker in message.stickers:
            content += f" [Sticker: {sticker.name}]"
        
        for embed in message.embeds:
            if embed.title:
                content += f" [Embed Title: {sanitize(embed.title)}]"
            if embed.description:
                content += f" [Embed Content: {sanitize(embed.description)}]"

        text_attachments = [att for att in message.attachments if att.content_type and att.content_type.startswith("text")]
        total_file_length = 0
        for text_file in text_attachments:
            fp = BytesIO()
            try:
                await text_file.save(fp, seek_begin=True)
                file_content = fp.getvalue().decode('utf-8')
            except (discord.DiscordException, UnicodeDecodeError):
                log.warning("Processing text attachments", exc_info=True)
            else:
                if len(file_content) > max_file_length + 10:
                    file_content = f"{file_content[:max_file_length//2]}\n(...)\n{file_content[-max_file_length//2:]}"
                total_file_length += len(file_content)
                content += f"\n[[[ Content of {text_file.filename}: {file_content} ]]]"
                if total_file_length > 4000:
                    break

        if quote and recursive:
            quote_content = await self.parse_discord_message(quote, None, trim_quote, False, max_quote_length, max_file_length)
            quote_content = quote_content.replace("\n", " ")
            if trim_quote and len(quote_content) > max_quote_length:
                quote_content = quote_content[:max_quote_length-3] + "..."
            content += f"\n[[[ Replying to: {quote_content} ]]]"            

        if len(content) == starting_len:
            content += " [Message empty or not supported]"

        mentions = message.mentions + message.role_mentions + message.channel_mentions
        for mentioned in mentions:
            if mentioned in message.channel_mentions:
                content = content.replace(mentioned.mention, f'#{mentioned.name}')
            elif mentioned in message.role_mentions:
                content = content.replace(mentioned.mention, f'@{mentioned.name}')
            else:
                content = content.replace(mentioned.mention, f'@{mentioned.name}')

        for message_link in DISCORD_MESSAGE_LINK_PATTERN.finditer(content):
            guild_id = int(message_link.group("guild_id"))
            channel_id = int(message_link.group("channel_id"))
            if message.guild.id != guild_id:
                replacement = "[Link to message outside server]"
            elif message.channel.id != channel_id:
                channel = message.guild.get_channel_or_thread(channel_id)
                replacement = f"[Link to message in #{channel.name}]" if channel else "[Link to message]"
            else:
                replacement = f"[Link to message]"
            content = content.replace(message_link.group(0), replacement)

        return content.strip()
