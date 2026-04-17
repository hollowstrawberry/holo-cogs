import json
import logging
import asyncio
import aiohttp
import discord
import tiktoken
import xmltodict
from io import BytesIO
from re import Match
from random import random
from typing import Any
from difflib import get_close_matches
from datetime import datetime, timezone
from itertools import chain
from expiringdict import ExpiringDict
from openai import AsyncOpenAI, NotGiven
from openai.types.chat import ChatCompletionMessageFunctionToolCall
from redbot.core import commands
from redbot.core.bot import Red

import gptmemory.utils as utils
import gptmemory.constants as constants
from gptmemory.schema import GptMemoryResult, GptMessage, GptImageContent, ImageGenParams, MemoryChangeList, MemoryChangeResult
from gptmemory.commands import GptMemoryCommands
from gptmemory.functions.base import get_all_function_calls
from gptmemory.functions.update_memory import UpdateMemoryFunctionCall
from gptmemory.views.memory_change import MemoryChangeView

log = logging.getLogger("gptmemory")


class GptMemory(GptMemoryCommands):
    """OpenAI-powered user with persistent memory and various tools."""

    def __init__(self, bot: Red):
        super().__init__(bot)
        self.image_cache: dict[int, GptImageContent] = ExpiringDict(max_len=50, max_age_seconds=24*60*60)
        self.available_function_calls = set(get_all_function_calls())
        self.encoding = tiktoken.get_encoding(constants.TOKEN_ENCODING)
        all_function_names = [tool.schema.function.name for tool in self.available_function_calls]
        log.info(f"{all_function_names=}")


    async def cog_load(self):
        await self.initialize_function_calls()
        await self.initialize_openai_client()
        self.extended_logging = await self.config.extended_logging()
        all_config = await self.config.all_guilds()
        for guild_id, config in all_config.items():
            self.memory[guild_id] = config["memory"]


    async def cog_unload(self):
        if self.session:
            await self.session.close()
        if self.openai_client:
            await self.openai_client.close()
        if self.openrouter_client:
            await self.openrouter_client.close()


    async def initialize_function_calls(self):
        all_function_calls = get_all_function_calls()
        self.available_function_calls = set(all_function_calls)
        for function in all_function_calls:
            for api in function.apis:
                secret = (await self.bot.get_shared_api_tokens(api[0])).get(api[1])
                if not secret:
                    self.available_function_calls.discard(function)


    async def initialize_openai_client(self):
        openai_api_key = (await self.bot.get_shared_api_tokens("openai")).get("api_key")
        if openai_api_key:
            if self.openai_client:
                await self.openai_client.close()
            self.openai_client = AsyncOpenAI(api_key=openai_api_key)
        openrouter_api_key = (await self.bot.get_shared_api_tokens("openrouter")).get("api_key")
        if openrouter_api_key:
            if self.openrouter_client:
                await self.openrouter_client.close()
            self.openrouter_client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_api_key)


    def get_client(self, model: str) -> AsyncOpenAI:
        client = self.openrouter_client if "/" in model else self.openai_client
        if client is None:
            raise RuntimeError(f"{'OpenRouter' if '/' in model else 'OpenAI'} client not initialized. Did you set up an api_key?")
        return client


    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name, _):
        await self.initialize_function_calls()
        if service_name in ("openai", "openrouter"):
            await self.initialize_openai_client()


    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        if message.id in self.currently_responding:
            return
        self.currently_responding.add(message.id)
        try:
            await self.handle_message(message)
        finally:
            self.currently_responding.discard(message.id)

    
    async def handle_message(self, message:discord.Message):
        ctx: commands.Context = await self.bot.get_context(message) 
        if not await self.is_valid_trigger(ctx):
            return

        assert ctx.guild and isinstance(ctx.channel, (discord.TextChannel, discord.Thread))

        if self.bot.user not in ctx.message.mentions:
            autoresponder_chance = await self.config.guild(ctx.guild).autoresponder_chance()
            cooldown_minutes = await self.config.guild(ctx.guild).autoresponder_cooldown_minutes()
            last_response = datetime.fromisoformat(await self.config.channel(ctx.channel).last_response())
            if random() > autoresponder_chance or (datetime.now(tz=timezone.utc) - last_response).total_seconds() < cooldown_minutes * 60:
                return False
            
        await self.config.channel(ctx.channel).last_response.set(datetime.now(tz=timezone.utc).isoformat())
        
        if match := constants.URL_PATTERN.search(message.content):
            if not message.embeds and f"<{match.group(0)}>" not in message.content:  # non-embedding links
                await ctx.channel.typing()
                ctx = await self.wait_for_embed(ctx)

        soft_timeout = await self.config.slow_timer()
        hard_timeout = await self.config.response_timeout()
        # run the task with soft timeout
        task = asyncio.create_task(self.run_response(ctx, auto=self.bot.user not in ctx.message.mentions))
        done, _ = await asyncio.wait([task], timeout=soft_timeout)
        # show the user if task is taking too long
        if task not in done:
            emoji = await self.config.slow_emoji()
            asyncio.create_task(ctx.message.add_reaction(emoji))
        # finish running the task with hard timeout, additionally reraise any previous exceptions, or do nothing if already finished
        try:
            await asyncio.wait_for(task, timeout=hard_timeout)
        except Exception:
            log.exception("run_response")
            # show the user if task didn't finish
            emoji = await self.config.noresponse_emoji()
            asyncio.create_task(ctx.message.add_reaction(emoji))
    
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
        if ctx.author.bot:
            return False
        if not ctx.guild:
            return False

        channel_mode = await self.config.guild(ctx.guild).channel_mode()
        channel_list = await self.config.guild(ctx.guild).channels()
        if channel_mode == "blacklist" and ctx.channel.id in channel_list \
                or channel_mode == "whitelist" and ctx.channel.id not in channel_list:
            return False

        if await self.bot.cog_disabled_in_guild(self, ctx.guild):
            return False
        if not await self.bot.ignored_channel_or_guild(ctx):
            return False
        if not await self.bot.allowed_by_whitelist_blacklist(ctx.author):
            return False

        if not self.openai_client and not self.openrouter_client:
            await self.initialize_openai_client()

        return True


    @staticmethod
    async def wait_for_embed(ctx: commands.Context) -> commands.Context:
        for _ in range(2):
            await asyncio.sleep(1)
            ctx.message = await ctx.channel.fetch_message(ctx.message.id)
            if ctx.message.embeds:
                break
        return ctx        


    async def run_response(self, ctx: commands.Context, auto: bool = False):
        assert ctx.guild
        self.memory.setdefault(ctx.guild.id, {})
        memory_names = list(self.memory[ctx.guild.id].keys())

        result = GptMemoryResult()
        mem_task = None
        async with ctx.typing():
            backread = await self.fetch_message_history(ctx)
            messages = await self.build_message_history_context(ctx, backread, result)
            participants = list(set([ctx.guild.get_member(msg.author.id) or msg.author for msg in backread]))
            recalled_memories = await self.execute_recaller(ctx, participants, messages, memory_names, result)
            recalled_memories_str = self.build_memory_string(memory_names, recalled_memories, ctx, participants)
            if not auto and await self.config.guild(ctx.guild).allow_memorizer():
                mem_task = asyncio.create_task(self.execute_memorizer(ctx, messages, memory_names, recalled_memories_str, result, standalone=True))
            await self.execute_responder(ctx, messages, memory_names, recalled_memories_str, result, auto)
        if mem_task:
            await mem_task
        log.info(result)


    async def execute_recaller(self,
                               ctx: commands.Context,
                               participants: list[discord.Member | discord.User],
                               messages: list[GptMessage],
                               memories: list[str],
                               result: GptMemoryResult
                               ) -> dict[str, str]:
        """
        Runs an openai completion with the chat history and a list of memories from the database
        and returns a dictionary of memories and their contents as chosen by the LLM.
        """
        assert ctx.guild
        if not memories:
            return {}

        temp_messages = utils.get_text_contents(messages)
        temp_memories = list(memories)
        memories_to_recall = set()
        participant_names = [p.name for p in participants]
        for memory in memories:
            if memory in participant_names:
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
        effort = utils.adjusted_effort(model, await self.config.guild(ctx.guild).effort_recaller())
        response = await self.get_client(model).beta.chat.completions.create(
            model=model,
            messages=temp_messages,  # type: ignore
            reasoning_effort=NotGiven() if "gpt-4" in model else effort  # type: ignore
        )
        completion = response.choices[0].message.content
        if completion:
            memories_to_recall.update([memory for memory in temp_memories if memory.lower() in completion.lower()])
        if response.usage:
            result.tokens.recaller = (response.usage.prompt_tokens, response.usage.completion_tokens)
        if self.extended_logging:
            log.info(f"{memories_to_recall=}")

        recalled_memories = {k: v for k, v in self.memory[ctx.guild.id].items() if k in memories_to_recall}
        return recalled_memories or {}
  

    async def execute_responder(self,
                                ctx: commands.Context,
                                messages: list[GptMessage],
                                memory_names: list[str],
                                recalled_memories_str: str,
                                result: GptMemoryResult,
                                auto: bool = False,
                                ) -> GptMessage:
        """
        Runs an openai completion with the chat history and the contents of memories
        and returns a response message after sending it to the user.
        """
        assert ctx.guild and isinstance(ctx.me, discord.Member) and isinstance(ctx.channel, (discord.TextChannel, discord.Thread))
        
        model = await self.config.guild(ctx.guild).model_responder()
        effort = utils.adjusted_effort(model, await self.config.guild(ctx.guild).effort_responder())
        max_tokens = await self.config.guild(ctx.guild).response_tokens()
        max_tool_depth = await self.config.guild(ctx.guild).max_tool_depth()
        max_tool_length = await self.config.guild(ctx.guild).max_tool()

        base_system_content = await self.config.guild(ctx.guild).prompt_autoresponder() if auto else await self.config.guild(ctx.guild).prompt_responder()
        prompt_keys = await self.config.guild(ctx.guild).prompt_keys()
        system_content = base_system_content.format(
            botname=ctx.me.name,
            botnickname=ctx.me.nick or ctx.me.name,
            servername=ctx.guild.name,
            channelname=ctx.channel.name,
            currentdatetime=datetime.now().strftime(constants.DATETIME_FORMATTING),
            memories=recalled_memories_str,
            **prompt_keys,
        )
        result.tokens.memories = len(self.encoding.encode(recalled_memories_str))
        result.tokens.system = len(self.encoding.encode(system_content)) - result.tokens.memories

        temp_messages = [msg for msg in messages]
        system_role = "developer" if "gpt-5" in model else "system"
        system_prompt = {
            "role": system_role,
            "content": system_content,
        }
        temp_messages.insert(0, system_prompt)
        if prompt_keys.get("end", "").strip():
            end_prompt = {
                "role": "system",
                "content": prompt_keys["end"],
            }
            temp_messages.append(end_prompt)
        if prompt_keys.get("prefill", "").strip():
            prefill_prompt = {
                "role": "assistant",
                "content": prompt_keys["prefill"],
            }
            temp_messages.append(prefill_prompt)

        disabled_functions = await self.config.guild(ctx.guild).disabled_functions()
        tools = [t for t in self.available_function_calls if t.schema.function.name not in disabled_functions]
        tools_schema = [t.asdict() for t in tools]
        result.tokens.schema = len(self.encoding.encode(json.dumps(tools_schema)))

        past_memory_changes: list[MemoryChangeResult] = []
        past_tool_calls: list[str] = []
        for depth in range(max_tool_depth):
            response = await self.get_client(model).chat.completions.create(
                model=model,
                messages=temp_messages,  # type: ignore
                stop=["</content>", "</chat_message>"],
                max_tokens=NotGiven() if "gpt-5" in model else max_tokens,  # type: ignore
                max_completion_tokens=NotGiven() if "gpt-5" not in model else max_tokens,  # type: ignore
                tools=tools_schema,  # type: ignore
                tool_choice="none" if depth >= max_tool_depth - 1 else "auto",
                reasoning_effort=NotGiven() if "gpt-4" in model else effort  # type: ignore
            )
            
            if response.usage:
                result.input_tokens += response.usage.prompt_tokens
                result.output_tokens += response.usage.completion_tokens
                if cost := getattr(response.usage, "cost", 0.0):
                    if isinstance(result.cost, str):
                        result.cost = 0.0
                    result.cost += cost
                if response.usage.prompt_tokens_details:
                    result.tokens.cached += response.usage.prompt_tokens_details.cached_tokens or 0
                if response.usage.completion_tokens_details:
                    result.tokens.thinking += response.usage.completion_tokens_details.reasoning_tokens or 0

            if not response.choices:  # request may get rejected
                error = str(getattr(response, "error", ""))
                if "403" in error or "PROHIBITED" in error:
                    log.warning(f"Missing response: {error}")
                    #await self.config.channel(ctx.channel).start.set(ctx.message.created_at.isoformat())  # failsafe so it doesn't keep getting blocked by the same stuff
                    emoji = await self.config.blocked_emoji()
                else:
                    log.error(f"Missing response: {error}")
                    emoji = await self.config.noresponse_emoji()
                await ctx.message.add_reaction(emoji)
                return {}

            if not response.choices[0].message.tool_calls:
                break
                  
            temp_messages.append(response.choices[0].message)  # type: ignore
            for call in response.choices[0].message.tool_calls:
                assert isinstance(call, ChatCompletionMessageFunctionToolCall)
                result.tool_calls += 1
                try:
                    cls = next(t for t in tools if t.schema.function.name == call.function.name)
                    if cls is UpdateMemoryFunctionCall:
                        if past_memory_changes:  # only allow one memory update per response
                            changes = []
                        else:
                            changes = await self.execute_memorizer(ctx, messages, memory_names, recalled_memories_str, result, standalone=False)
                            past_memory_changes += changes
                        args = {"changes": changes}
                    else:
                        args = json.loads(call.function.arguments)
                    tool_result = await cls(ctx, self).run(args)
                except Exception:  # tools should handle specific errors internally, but broad errors should not stop the responder
                    tool_result = "<error>Unhandled error, please contact the developer</error>"
                    log.exception(f"Calling tool {call.function.name}")

                past_tool_calls.append(call.function.name)
                if isinstance(tool_result, dict):
                    if len(tool_result) == 0:
                        tool_result = {"result": "None"}
                    elif len(tool_result) > 1:
                        tool_result = {"result": tool_result}
                    tool_text = xmltodict.unparse(tool_result, full_document=False)
                else:
                    tool_text = tool_result.strip()

                if len(tool_text) > max_tool_length:
                    tool_text = utils.fix_truncated_xml(tool_text[:max_tool_length]) + "..."
                result.tokens.tools += len(self.encoding.encode(tool_text))
                log.info(f"{call.function.name=} {call.function.arguments=}")
                if self.extended_logging:
                    log.info(f"{tool_text=}")
              
                temp_messages.append({
                    "role": "tool",
                    "content": tool_text,
                    "tool_call_id": call.id,
                })

        completion = response.choices[0].message.content or ""
        if completion:
            raw_completion = completion
            if self.extended_logging:
                log.info(f"{raw_completion=}")
            # special case: the bot tries to generate an image by sending text instead of using the function call
            prompt = None
            for pattern in constants.GENERATE_IMAGE_PATTERNS.values():
                if m := pattern.search(completion):
                    prompt = utils.undo_xml(m.groups()[-1])
                    completion = pattern.sub("", completion)
            if prompt and "generate_stable_diffusion" not in past_tool_calls:
                await self.generate_stable_diffusion(ctx, prompt)
            # cleanup
            for _, pattern, repl in constants.RESPONSE_CLEANUP_PATTERNS:
                completion = pattern.sub(repl, completion)
            cleaned_completion = completion
            if raw_completion != cleaned_completion:
                log.info(f"{cleaned_completion=}")
            def fix_emote(match: Match) -> str:
                log.info(f"emote={match.group(1)}")
                emote = discord.utils.get(ctx.bot.emojis, name=match.group(1))
                return str(emote) if emote else match.group(0)
            completion = constants.INCOMPLETE_EMOTE_PATTERN.sub(fix_emote, completion)
            if cleaned_completion != completion:
                log.info(f"emote_cleaned_{completion=}")
            completion = utils.undo_xml(completion).strip()

        view = MemoryChangeView(past_memory_changes, standalone=False) if past_memory_changes else None
        if completion or view:
            await utils.chunk_and_send(ctx, completion, embed=None, view=view, do_reply=not auto)
        else:
            emoji = await self.config.noresponse_emoji()
            await ctx.message.add_reaction(emoji)

        response_message = {
            "role": "assistant",
            "content": completion
        }
        return response_message  # type: ignore


    async def execute_memorizer(self,
                                ctx: commands.Context,
                                messages: list[GptMessage],
                                memory_names: list[str],
                                recalled_memories_str: str,
                                result: GptMemoryResult,
                                standalone: bool
                                ) -> list[MemoryChangeResult]:
        """
        Runs an openai completion with the chat history, a list of memories, and the contents of some memories,
        and executes database operations as decided by the LLM.
        """
        assert ctx.guild and ctx.guild.me

        if await self.config.guild(ctx.guild).memorizer_user_only():
            memory_names = [memory for memory in memory_names if any(member.name == memory for member in ctx.guild.members)]
        memory_names_obj = {
            "memory_names": {
                "#text": ", ".join(memory_names),
            }
        }
        system_content = (await self.config.guild(ctx.guild).prompt_memorizer()).format(
            xmltodict.unparse(memory_names_obj, full_document=False),
            recalled_memories_str,
            botname=ctx.me.name,
            botnickname=ctx.me.nick or ctx.me.name
        )
        system_prompt = {
            "role": "system",
            "content": system_content
        }

        prefixes = await self.bot.get_valid_prefixes(ctx.guild)
        def is_valid(msg: GptMessage) -> bool:
            if msg["role"] == "user" and any(f"<content>{prefix}" in msg["content"] for prefix in prefixes):  # bot command
                return False
            return True
        temp_messages = [msg for msg in utils.get_text_contents(messages) if is_valid(msg)]
        num_backread = await self.config.guild(ctx.guild).backread_memorizer()
        if len(temp_messages) > num_backread:
            temp_messages = temp_messages[-num_backread:]
        temp_messages.insert(0, system_prompt)

        model = await self.config.guild(ctx.guild).model_memorizer()
        effort = utils.adjusted_effort(model, await self.config.guild(ctx.guild).effort_memorizer())
        response = await self.get_client(model).beta.chat.completions.parse(
            model=model,
            messages=temp_messages,  # type: ignore
            response_format=MemoryChangeList,
            reasoning_effort=NotGiven() if "gpt-4" in model else effort  # type: ignore
        )
        completion = response.choices[0].message
        if response.usage:
            result.tokens.memorizer = (response.usage.prompt_tokens, response.usage.completion_tokens)
        if completion.refusal:
            log.warning(completion.refusal)
            return []
        if not completion.parsed or not completion.parsed.memory_changes:
            return []

        memory_changes: list[MemoryChangeResult] = []
        async with self.config.guild(ctx.guild).memory() as memory:
            memory: dict[str, str]
            for change in completion.parsed.memory_changes:
                action, name, content = change.action_type, change.memory_name, change.memory_content

                if name not in memory and action != "create":
                    matches = get_close_matches(name, memory)
                    if not matches:
                        continue
                    name = matches[0]

                content = content.strip()
                before = memory.get(name)
                if action == "delete":
                    del memory[name]
                    del self.memory[ctx.guild.id][name]
                elif action == "create" and name not in memory:
                    memory[name] = content
                    self.memory[ctx.guild.id][name] = content
                elif action == "modify" and name in memory:
                    memory[name] = content
                    self.memory[ctx.guild.id][name] = content
                else:
                    memory[name] += " ... " + content
                    self.memory[ctx.guild.id][name] += " ... " + content

                if self.extended_logging:
                    log.info(f"{action} memory / {name=} / {content=}")

                after = memory.get(name)
                if before != after:
                    memory_changes.append(MemoryChangeResult(name, before, after))

        if standalone and memory_changes and await self.config.guild(ctx.guild).memorizer_alerts():
            view = MemoryChangeView(memory_changes, standalone)
            view.message = await ctx.send(view=view)
        return memory_changes


    async def build_message_history_context(self, ctx: commands.Context, backread: list[discord.Message], result: GptMemoryResult) -> list[GptMessage]:
        assert ctx.guild and self.bot.user
        messages = []
        processed_image_sources = []
        tokens = 0
        total_images = 0

        max_image_size = await self.config.guild(ctx.guild).max_image_resolution()
        max_images = await self.config.guild(ctx.guild).max_images()
        max_quote_length = await self.config.guild(ctx.guild).max_quote()
        max_file_length = await self.config.guild(ctx.guild).max_text_file()
        max_backread_tokens = await self.config.guild(ctx.guild).backread_tokens()
        for n, backmsg in enumerate(backread):
            quote = None
            if backmsg.reference and not (len(backread) > n+1 and backmsg.reference.message_id == backread[n+1].id):  # don't chain consecutive quotes
                try:
                    quote = backmsg.reference.cached_message or await backmsg.channel.fetch_message(backmsg.reference.message_id)  # type: ignore
                except discord.DiscordException:
                    pass
            images_left = max_images - total_images
            if images_left > 0:
                image_contents = await self.extract_images(backmsg, quote, processed_image_sources, images_left, max_image_size)
                total_images += len(image_contents)
            else:
                image_contents = []
            message_obj, message_inline_objs = await self.parse_discord_message(backmsg, quote, backread, max_quote_length, max_file_length, exhaustive=True, recursive=True)
            text_content = xmltodict.unparse(message_obj, full_document=False)
            for before, after_obj in message_inline_objs.items():
                text_content = text_content.replace(before, xmltodict.unparse(after_obj, full_document=False))

            if image_contents:
                image_contents.insert(0, {
                    "type": "text",
                    "text": text_content
                })
                messages.append({
                    "role": "user", # assistant can't have image contents
                    "content": image_contents
                })
            else:
                messages.append({
                    "role": "assistant" if backmsg.author.id == self.bot.user.id else "user",
                    "content": text_content
                })

            text_tokens = len(self.encoding.encode(text_content))
            image_tokens = 1120 * max(0, len(image_contents) - 1)
            tokens += text_tokens + image_tokens
            if n > 0 and tokens > max_backread_tokens:
                break
        
        image_sources = [att.url if isinstance(att, discord.Attachment) else att for att in processed_image_sources]
        if self.extended_logging:
            log.info(f"{image_sources=}")
        result.tokens.backread = tokens
        result.images = total_images
        result.messages = len(messages)

        return list(reversed(messages))


    async def fetch_message_history(self, ctx: commands.Context) -> list[discord.Message]:
        assert ctx.guild and isinstance(ctx.channel, (discord.TextChannel, discord.Thread))
        limit = await self.config.guild(ctx.guild).backread_messages()
        after = datetime.fromisoformat(await self.config.channel(ctx.channel).start())
        backread = [message async for message in ctx.channel.history(
            limit=limit,
            before=ctx.message,
            after=after,
            oldest_first=False
        )]
        backread.insert(0, ctx.message)
        return backread


    async def extract_images(self,
                             message: discord.Message,
                             quote: discord.Message | None,
                             processed_sources: list[str | discord.Attachment],
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
                imagescanner: commands.Cog | None = self.bot.get_cog("ImageScanner")
                if imagescanner and message.id in imagescanner.image_cache: # type: ignore
                    _, image_bytes = self.image_cache.get(message.id, ({}, {}))
                    if i in image_bytes:
                        fp_before = BytesIO(image_bytes[i]) # type: ignore
                if fp_before.getbuffer().nbytes == 0:
                    try:
                        await image.save(fp_before, seek_begin=True)
                    except discord.DiscordException as error:
                        log.warning(f"Processing image attachments: {type(error).__name__}: {error}")
                        continue

                fp_after = await asyncio.to_thread(utils.normalize_image, fp_before, max_image_size**2)
                del fp_before
                if not fp_after:
                    continue

                image_contents.append(utils.make_image_content(fp_after))
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

        matches = constants.URL_PATTERN.findall(message.content)
        for match in matches:
            if match.endswith(constants.IMAGE_EXTENSIONS):
                image_url.append(match)

        if not image_url:
            return image_contents

        for url in image_url[:max_images]:
            if url in processed_sources:
                continue
            processed_sources.append(url)
            try:
                async with self.session.get(url) as response:
                    response.raise_for_status()
                    fp_before = BytesIO(await response.read())
            except aiohttp.ClientError as error:
                log.warning(f"Processing image {url}: {type(error).__name__}: {error}")
                continue
            fp_after = await asyncio.to_thread(utils.normalize_image, fp_before, max_image_size**2)
            del fp_before
            if not fp_after:
                continue
            image_contents.append(utils.make_image_content(fp_after))
            del fp_after

        if image_contents:
            self.image_cache[message.id] = [cnt for cnt in image_contents]

        return image_contents


    async def parse_discord_message(self,
                                    message: discord.Message,
                                    quote:  discord.Message | None,
                                    backread: list[discord.Message],
                                    max_quote_length: int,
                                    max_file_length: int,
                                    exhaustive: bool,
                                    recursive: bool,
                                    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        """
        Converts a message into a dictionary of structured information that may then be unparsed into xml.
        Also returns a dictionary of inline objects to be injected back into the final string.
        """
        assert message.guild
        inline_objs: dict[str, dict[str, Any]] = {}
        obj: dict[str, Any] = {
            "@time": message.created_at.astimezone().strftime(constants.DATETIME_FORMATTING),
            "@username": message.author.name,
        }
        if isinstance(message.author, discord.Member) and message.author.nick:
            obj["@nickname"] = message.author.nick
        starting_len = len(obj)
        if message != backread[0] and (message.id in self.currently_responding or message.id in self.currently_generating):
            obj["error"] = "This message is currently being processed"
            return ({"chat_message": obj}, inline_objs)
        # generated image
        if message.attachments and len(message.attachments) == 1 and message.author == message.guild.me:
            imagescanner: commands.Cog | None = self.bot.get_cog("ImageScanner")
            metadata: dict[str, Any] = await imagescanner.grab_metadata_dict(message)  # type: ignore
            if metadata and metadata.get("Prompt"):
                obj["generated_image"] = {
                    "@filename": message.attachments[0].filename,
                    "@dimensions": metadata.get("Size", "unknown"),
                    "prompt": utils.parse_prompt(metadata["Prompt"]),
                }
        # quote
        if quote and exhaustive and recursive and "generated_image" not in obj:
            quoted_message_obj, quoted_message_inlines = await self.parse_discord_message(quote, None, backread, max_quote_length, max_file_length, exhaustive=quote not in backread, recursive=False)
            obj["quote"] = quoted_message_obj
            inline_objs.update(quoted_message_inlines)
        # text content
        if message.is_system():
            obj["action"] = "Joined the server" if message.type == discord.MessageType.new_member else message.system_content
        elif message.content:
            content = message.content
            for mentioned in message.mentions + message.role_mentions:
                content = content.replace(mentioned.mention, f"@{mentioned.name}")
            for mentioned_ch in message.channel_mentions:
                content = content.replace(mentioned_ch.mention, f"#{mentioned_ch.name}")
            for i, message_link in enumerate(constants.DISCORD_MESSAGE_LINK_PATTERN.finditer(content)):
                guild_id, channel_id, message_id = [int(num) for num in message_link.groups()]
                link_obj = {}
                if message.guild.id != guild_id:
                    link_obj["@source"] = "Outside this server"
                elif message.channel.id != channel_id:
                    channel = message.guild.get_channel_or_thread(channel_id)
                    link_obj["@channel"] = f"#{channel.name}" if channel else "unknown"
                inline_objs[message_link.group(0)] = {
                    "message_link": {
                        "#text": "...",
                        **link_obj,
                    }}
                # Add quote for linked message if it is the first
                if i == 0 and exhaustive and recursive and "generated_image" not in obj:
                    try:
                        linked = await self.bot.get_guild(guild_id).get_channel(channel_id).fetch_message(message_id) # type: ignore
                    except (AttributeError, discord.NotFound):
                        continue
                    linked_message_obj, linked_message_inlines = await self.parse_discord_message(linked, None, backread, max_quote_length, max_file_length, exhaustive=linked not in backread, recursive=False)
                    obj["linked_message"] = {**link_obj, **linked_message_obj}
                    inline_objs.update(linked_message_inlines)
            if not exhaustive and len(content) > max_quote_length:
                content = content[:max_quote_length - 3] + "..."
                obj["@truncated"] = "true"
            obj["content"] = content
        # attachments
        if "generated_image" not in obj:
            attachments = []
            total_file_length = 0
            for attachment in message.attachments:
                att_obj = {"@filename": attachment.filename}
                if exhaustive and attachment.content_type and attachment.content_type.startswith("text") and total_file_length < max_file_length:
                    if file_content := await self.read_text_file(attachment, max_file_length):
                        att_obj["content"] = file_content
                attachments.append(att_obj)
            utils.add_xml_group(obj, attachments, "attachments")
        # stickers
        stickers = []
        for sticker in message.stickers:
            stickers.append({"#text": sticker.name})
        utils.add_xml_group(obj, stickers, "stickers")
        # embeds
        embeds = []
        for embed in message.embeds:
            embed_obj = {}
            if embed.title:
                embed_obj["title"] = embed.title
            if embed.description:
                embed_obj["description"] = embed.description if exhaustive else "..."
            if embed.image and embed.image.url:
                embed_obj["image"] = embed.image.url
            if embed.thumbnail and embed.thumbnail.url:
                embed_obj["thumbnail"] = embed.thumbnail.url
            fields = []
            for field in embed.fields:
                fields.append({
                    "@name": field.name,
                    "#text": field.value,
                })
            if len(fields) > 0 and exhaustive:
                utils.add_xml_group(embed_obj, fields, "fields")
            if embed_obj:
                embeds.append(embed_obj)
        utils.add_xml_group(obj, embeds, "embeds")
        # buttons
        if exhaustive and "generated_image" not in obj:
            buttons = []
            for component in message.components:
                if isinstance(component, discord.ActionRow):
                    for subcomponent in component.children:
                        if isinstance(subcomponent, discord.Button):
                            buttons.append({"#text": utils.button_label(subcomponent)})
                elif isinstance(component, discord.Button):
                    buttons.append({"#text": utils.button_label(component)})
            utils.add_xml_group(obj, buttons, "buttons")
        # poll
        if message.poll:
            poll = {"question": message.poll.question}
            if exhaustive:
                answers = []
                for answer in message.poll.answers:
                    answers.append({
                        "@votes": str(answer.vote_count),
                        "#text": answer.text,
                    })
                utils.add_xml_group(poll, answers, "answers")
            obj["poll"] = poll
        # etc
        if len(obj) == starting_len:
            obj["error"] = "Message empty or not supported"
        # reactions
        if exhaustive and "generated_image" not in obj:
            reactions = []
            for reaction in message.reactions[:5]:
                reaction_obj = {
                    "@count": str(reaction.count),
                    "#text": reaction.emoji if isinstance(reaction.emoji, str) else reaction.emoji.name
                }
                if reaction.me:
                    reaction_obj["self_reacted"] = "true"
                reactions.append(reaction_obj)
            utils.add_xml_group(obj, reactions, "reactions")
        # done
        return ({"chat_message": obj}, inline_objs)
    

    def build_memory_string(self, memory_names: list[str], recalled_memories: dict[str, str], ctx: commands.Context, participants: list[discord.Member | discord.User]) -> str:
        assert ctx.guild
        recalled_memories_obj = {
            "memories": {
                "memory": []
            }
        }
        member_names = [member.name for member in ctx.guild.members]
        temp: dict[str, dict[str, str]] = {}
        for name, content in recalled_memories.items():
            if name not in memory_names:
                continue
            name = name.strip()
            temp.setdefault(name, {})
            if content and content.strip():
                temp[name]["#text"] = content.strip()
            if name in member_names:
                temp[name]["@user"] = name
            else:
                temp[name]["@topic"] = name
        for member in participants:
            if not isinstance(member, discord.Member) or member == ctx.guild.me:
                continue
            temp.setdefault(member.name, {})
            temp[member.name]["@user"] = member.name
            perms = ctx.channel.permissions_for(member)
            if member == ctx.guild.owner:
                temp[member.name]["@role"] = "server owner"
            elif perms.administrator:
                temp[member.name]["@role"] = "administrator"
            elif perms.manage_messages:
                temp[member.name]["@role"] = "moderator"
        for mem_obj in temp.values():
            if len(mem_obj) > 1:
                recalled_memories_obj["memories"]["memory"].append(mem_obj)
        return xmltodict.unparse(recalled_memories_obj, full_document=False)
    

    async def read_text_file(self, attachment: discord.Attachment, max_file_length: int) -> str | None:
        fp = BytesIO()
        try:
            await attachment.save(fp, seek_begin=True)
            file_content = fp.getvalue().decode('utf-8')
        except (discord.DiscordException, UnicodeDecodeError) as error:
            log.warning(f"Processing text attachment {attachment.filename}: {type(error).__name__}: {error}")
            return None
        if len(file_content) > max_file_length + 10:
            file_content = f"{file_content[:max_file_length//2]}\n(...)\n{file_content[-max_file_length//2:]}"
        return file_content


    async def generate_stable_diffusion(self, ctx: commands.Context, prompt: str):
        assert ctx.guild and self.bot.user

        channel_mode = await self.config.guild(ctx.guild).generation_channel_mode()
        channels = await self.config.guild(ctx.guild).generation_channels()
        if channel_mode == "blacklist" and ctx.channel.id in channels \
                or channel_mode == "whitelist" and ctx.channel.id not in channels:
            if ctx.bot_permissions.add_reactions:
                await ctx.message.add_reaction("❌")
            return
        
        aimage: commands.Cog | None = ctx.bot.get_cog("AImage")
        if not aimage:
            await ctx.message.add_reaction("❌")
            return

        width, height = await self.find_last_generated_image_resolution(ctx)                
        params = ImageGenParams(
            prompt=prompt,
            width=width,
            height=height,
        )
        message_content = f"Requested at {ctx.message.jump_url} by {ctx.author.mention}"
        async def callback():
            await asyncio.sleep(0)
            self.currently_generating.discard(ctx.message.id)
        self.currently_generating.add(ctx.message.id)
        task = aimage.generate_image(ctx, params=params, message_content=message_content, callback=callback())  # type: ignore
        asyncio.create_task(task)


    async def find_last_generated_image_resolution(self, ctx: commands.Context) -> tuple[int | None, int | None]:
        backread = await self.fetch_message_history(ctx)
        if ctx.message.reference and (ctx.message.reference.cached_message or ctx.message.reference.message_id):
            quote = ctx.message.reference.cached_message or await ctx.message.channel.fetch_message(ctx.message.reference.message_id)  # type: ignore
            backread.insert(1, quote)
        for msg in backread[1:]:
            if msg.author == self.bot.user and msg.attachments and len(msg.attachments) == 1 and msg.attachments[0].width and msg.attachments[0].height:
                width, height = msg.attachments[0].width, msg.attachments[0].height
                if (width, height) not in constants.IMAGEGEN_RESOLUTIONS:
                    width, height = utils.find_nearest_resolution((width, height), constants.IMAGEGEN_RESOLUTIONS)
                return width, height
        return None, None
