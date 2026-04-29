import time
import json
import logging
import asyncio
import discord
import tiktoken
import xmltodict
from random import random
from difflib import get_close_matches
from datetime import datetime, timezone
from openai import AsyncOpenAI, NotGiven
from openai.types.chat import ChatCompletionMessageFunctionToolCall
from redbot.core import commands
from redbot.core.bot import Red

import gptmemory.utils as utils
import gptmemory.constants as constants
from gptmemory.schema import GptImageContent, GptMessage, CompletionResult, MemoryChangeResult, MemoryChangeList, ImageGenParams
from gptmemory.commands import GptMemoryCommands
from gptmemory.tools.base import get_all_tools
from gptmemory.tools.update_memory import UpdateMemoryTool
from gptmemory.context_builder import ContextBuilder
from gptmemory.views.memory_change import MemoryChangeView

log = logging.getLogger("gptmemory")


class GptMemory(GptMemoryCommands):
    """OpenAI-powered user with persistent memory and various tools."""

    def __init__(self, bot: Red):
        super().__init__(bot)
        self.encoding = tiktoken.get_encoding(constants.TOKEN_ENCODING)
        self.context_builder = ContextBuilder(self.bot, self.config, self.session, self.encoding, self.execute_captioner, self.is_busy)
        self.available_tools = set(get_all_tools())
        all_tool_names = [tool.display_name for tool in self.available_tools]
        log.info(f"{all_tool_names=}")


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
        if self.openwebui_client:
            await self.openwebui_client.close()


    async def initialize_function_calls(self):
        all_function_calls = get_all_tools()
        self.available_tools = set(all_function_calls)
        for function in all_function_calls:
            for api in function.apis:
                secret = (await self.bot.get_shared_api_tokens(api[0])).get(api[1])
                if not secret:
                    self.available_tools.discard(function)


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
        openwebui_credentials = await self.bot.get_shared_api_tokens("openwebui")
        if openwebui_credentials:
            if self.openwebui_client:
                await self.openwebui_client.close()
            self.openwebui_client = AsyncOpenAI(
                base_url=openwebui_credentials.get("endpoint"),
                api_key=openwebui_credentials.get("api_key"),
                default_headers={
                    "CF-Access-Client-Id": openwebui_credentials.get("cf_client_id") or "",
                    "CF-Access-Client-Secret": openwebui_credentials.get("cf_client_secret") or "",
                },
            )


    def get_client(self, model: str) -> AsyncOpenAI:
        if "$" in model:
            if not self.openwebui_client:
                raise RuntimeError("OpenWebui client is not initialized, please set up credentials including endpoint, api_key, cf_client_id, and cf_client_secret")
            return self.openwebui_client
        elif "/" in model:
            if not self.openrouter_client:
                raise RuntimeError("OpenRouter client is not initialized, did you set an api_key?")
            return self.openrouter_client
        else:
            if not self.openai_client:
                raise RuntimeError("OpenAI client is not initialized, did you set an api_key?")
            return self.openai_client


    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name, _):
        await self.initialize_function_calls()
        if service_name in ("openai", "openrouter", "openwebui"):
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
        start = time.perf_counter()
        result = CompletionResult()
        mem_task = None
        async with ctx.typing():
            backread = await self.fetch_message_history(ctx)
            messages = await self.context_builder.build_message_history_context(ctx, backread, result)
            participants = list(set([ctx.guild.get_member(msg.author.id) or msg.author for msg in backread]))
            recalled_memories = await self.execute_recaller(ctx, participants, messages, memory_names, result)
            recalled_memories_str = self.build_memory_string(memory_names, recalled_memories, ctx, participants)
            if not auto and await self.config.guild(ctx.guild).allow_memorizer():
                mem_task = asyncio.create_task(self.execute_memorizer(ctx, messages, memory_names, recalled_memories_str, result, standalone=True))
            await self.execute_responder(ctx, messages, memory_names, recalled_memories_str, result, auto)
        if mem_task:
            await mem_task
        result.elapsed_ms = int(1000 * (time.perf_counter() - start))
        log.info(result)


    async def execute_recaller(self,
                               ctx: commands.Context,
                               participants: list[discord.Member | discord.User],
                               messages: list[GptMessage],
                               memories: list[str],
                               result: CompletionResult
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
            model=utils.clean_model(model),
            messages=temp_messages,  # type: ignore
            reasoning_effort=NotGiven() if "gpt-4" in model else effort,  # type: ignore
            extra_body=None if "/" not in model else {
                "session_id": str(ctx.message.id),
            },
        )
        completion = response.choices[0].message.content
        if completion:
            memories_to_recall.update([memory for memory in temp_memories if memory.lower() in completion.lower()])
        if response.usage:
            result.tokens.recaller = (response.usage.prompt_tokens, response.usage.completion_tokens)
            if cost := getattr(response.usage, "cost", 0.0):
                result.add_cost(cost)
        if self.extended_logging:
            log.info(f"{memories_to_recall=}")

        recalled_memories = {k: v for k, v in self.memory[ctx.guild.id].items() if k in memories_to_recall}
        return recalled_memories or {}
  

    async def execute_responder(self,
                                ctx: commands.Context,
                                messages: list[GptMessage],
                                memory_names: list[str],
                                recalled_memories_str: str,
                                result: CompletionResult,
                                auto: bool = False,
                                ):
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

        enabled_functions = await self.config.guild(ctx.guild).enabled_functions()
        tools = [t for t in self.available_tools if t.display_name in enabled_functions]
        tools_schema = [t.asdict() for t in tools]
        result.tokens.schema = len(self.encoding.encode(json.dumps(tools_schema)))

        past_memory_changes: list[MemoryChangeResult] = []
        past_tool_calls: list[str] = []
        for depth in range(max_tool_depth):
            can_use_tools = depth < max_tool_depth - 1
            response = await self.get_client(model).chat.completions.create(
                model=utils.clean_model(model),
                messages=temp_messages,  # type: ignore
                max_tokens=NotGiven() if "gpt-5" in model else max_tokens,  # type: ignore
                max_completion_tokens=NotGiven() if "gpt-5" not in model else max_tokens,  # type: ignore
                tools=tools_schema if can_use_tools else None,  # type: ignore
                tool_choice="auto" if can_use_tools else "none",
                reasoning_effort=NotGiven() if "gpt-4" in model else effort,  # type: ignore
                extra_body=None if "/" not in model else {
                    "session_id": str(ctx.message.id),
                },
            )
            if response is None:
                log.error(f"OpenAI SDK returned NoneType")
                return
            
            if response.usage:
                result.input_tokens += response.usage.prompt_tokens
                result.output_tokens += response.usage.completion_tokens
                if cost := getattr(response.usage, "cost", 0.0):
                    result.add_cost(cost)
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
                    if cls is UpdateMemoryTool:
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
            for _, pattern in constants.GENERATE_IMAGE_PATTERNS:
                if m := pattern.search(completion):
                    prompt = utils.undo_xml(m.groups()[-1])
                    completion = pattern.sub("", completion)
                    break
            if prompt and "generate_stable_diffusion" not in past_tool_calls:
                await self.generate_stable_diffusion(ctx, prompt)
            # cleanup
            for _, pattern, repl in constants.RESPONSE_CLEANUP_PATTERNS:
                completion = pattern.sub(repl, completion)
            completion = constants.INCOMPLETE_EMOTE_PATTERN.sub(utils.fix_emote(ctx.bot), completion)
            completion = constants.FAKE_EMOTE_PATTERN.sub("\n", completion)
            completion = utils.undo_xml(completion).strip()
            if self.extended_logging and completion != raw_completion:
                log.info(f"cleaned_{completion=}")

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
                                result: CompletionResult,
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
            botnickname=ctx.guild.me.nick or ctx.me.name
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
            model=utils.clean_model(model),
            messages=temp_messages,  # type: ignore
            response_format=MemoryChangeList,
            reasoning_effort=NotGiven() if "gpt-4" in model else effort,  # type: ignore
            extra_body=None if "/" not in model else {
                "session_id": str(ctx.message.id),
            },
        )
        completion = response.choices[0].message
        if response.usage:
            result.tokens.memorizer = (response.usage.prompt_tokens, response.usage.completion_tokens)
            if cost := getattr(response.usage, "cost", 0.0):
                result.add_cost(cost)
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
    

    async def execute_captioner(self, ctx: commands.Context, image: GptImageContent, result: CompletionResult) -> str:
        assert ctx.guild
        messages: list[GptMessage] = [
            {
                "role": "system",
                "content": await self.config.guild(ctx.guild).prompt_captioner()
            },
            {
                "role": "user",
                "content": [image]
            }
        ]
        model = await self.config.guild(ctx.guild).model_captioner()
        effort = utils.adjusted_effort(model, "none")
        response = await self.get_client(model).beta.chat.completions.create(
            model=utils.clean_model(model),
            messages=messages,  # type: ignore
            reasoning_effort=NotGiven() if "gpt-4" in model else effort,  # type: ignore
            extra_body=None if "/" not in model else {
                "session_id": str(ctx.message.id),
            },
        )
        if response.choices and response.choices[0].message.content:
            caption = response.choices[0].message.content
        else:
            caption = "Unidentified image"
        if self.extended_logging:
            log.info(f"{caption=}")
        if response.usage:
            if cost := getattr(response.usage, "cost", 0.0):
                result.add_cost(cost)
            tokens = (response.usage.prompt_tokens, response.usage.completion_tokens)
            if isinstance(result.tokens.captioner, tuple):
                result.tokens.captioner = (result.tokens.captioner[0] + tokens[0], result.tokens.captioner[1] + tokens[1])
            else:
                result.tokens.captioner = tokens
        return caption


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

        width, height = await self.find_last_sd_generated_image_resolution(ctx)                
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
        generate_image = getattr(aimage, "generate_image")
        asyncio.create_task(generate_image(ctx, params=params, message_content=message_content, callback=callback()))


    async def find_last_sd_generated_image_resolution(self, ctx: commands.Context) -> tuple[int | None, int | None]:
        backread = await self.fetch_message_history(ctx)
        if ctx.message.reference and (ctx.message.reference.cached_message or ctx.message.reference.message_id):
            quote = ctx.message.reference.cached_message or await ctx.message.channel.fetch_message(ctx.message.reference.message_id or 0)
            backread.insert(1, quote)
        for msg in backread[1:]:
            if msg.author == self.bot.user and msg.attachments and len(msg.attachments) == 1 and msg.attachments[0].width and msg.attachments[0].height:
                width, height = msg.attachments[0].width, msg.attachments[0].height
                if (width, height) not in constants.SD_IMAGEGEN_RESOLUTIONS:
                    width, height = utils.find_nearest_resolution((width, height), constants.SD_IMAGEGEN_RESOLUTIONS)
                return width, height
        return None, None
