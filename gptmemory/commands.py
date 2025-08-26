import discord
from typing import Literal, Optional, Dict
from difflib import get_close_matches
from redbot.core import commands, Config
from redbot.core.bot import Red

import gptmemory.defaults as defaults
import gptmemory.constants as constants
from gptmemory.functions.base import get_all_function_calls


class GptMemoryBase(commands.Cog):
    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=19475820)
        self.config.register_guild(**{
            "channel_mode": "whitelist",
            "channels": [],
            "memory": {},
            "model_recaller": defaults.MODEL_RECALLER,
            "model_responder": defaults.MODEL_RESPONDER,
            "model_memorizer": defaults.MODEL_MEMORIZER,
            "prompt_recaller": defaults.PROMPT_RECALLER,
            "prompt_responder": defaults.PROMPT_RESPONDER,
            "prompt_memorizer": defaults.PROMPT_MEMORIZER,
            "effort_recaller": defaults.EFFORT_RECALLER,
            "effort_responder": defaults.EFFORT_RESPONDER,
            "effort_memorizer": defaults.EFFORT_MEMORIZER,
            "response_tokens": defaults.RESPONSE_TOKENS,
            "backread_tokens": defaults.BACKREAD_TOKENS,
            "backread_messages": defaults.BACKREAD_MESSAGES,
            "backread_memorizer": defaults.BACKREAD_MEMORIZER,
            "allow_memorizer": defaults.ALLOW_MEMORIZER,
            "memorizer_user_only": defaults.MEMORIZER_USER_ONLY,
            "memorizer_alerts": defaults.MEMORIZER_ALERTS,
            "disabled_functions": list(defaults.DISABLED_FUNCTIONS),
            "emotes": "",
            "max_images_per_message": defaults.IMAGES_PER_MESSAGE,
            "max_images": defaults.IMAGES_PER_CONTEXT,
            "max_quote": defaults.QUOTE_LENGTH,
            "max_tool": defaults.TOOL_CALL_LENGTH,
            "max_text_file": defaults.TEXT_FILE_LENGTH,
            "max_image_resolution": defaults.IMAGE_SIZE,
        })
        self.memory: Dict[int, Dict[str, str]] = {}

    @commands.command(name="memory", aliases=["memories"], invoke_without_subcommand=True)
    @commands.guild_only()
    async def command_memory(self, ctx: commands.Context, *, name: Optional[str]):
        """View all memories or a specific memory, of the GPT bot."""
        assert ctx.guild
        if not name:
            if ctx.guild.id in self.memory and self.memory[ctx.guild.id]:
                memories = self.memory[ctx.guild.id].keys()
                user_memories = [memory for memory in memories if any(member.name == memory for member in ctx.guild.members)]
                memories = [memory for memory in memories if memory not in user_memories]
                reply = "`[Memories:]`\n> " + ", ".join(f"`{mem}`" for mem in memories) \
                    + "\n\n`[User memories:]`\n> " + ", ".join(f"`{mem}`" for mem in user_memories)
                return await ctx.send(reply)
            else:
                return await ctx.send("No memories...")
        if ctx.guild.id in self.memory:
            if name not in self.memory[ctx.guild.id]:
                matches = get_close_matches(name, self.memory[ctx.guild.id])
                if matches:
                    name = matches[0]
            if name in self.memory[ctx.guild.id]:
                return await ctx.send(f"`[Memory of {name}]`\n>>> {self.memory[ctx.guild.id][name]}")
        await ctx.send(f"No memory of {name}")

    @commands.command(name="deletememory", aliases=["delmemory"]) # type: ignore
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def command_deletememory(self, ctx: commands.Context, *, name: str):
        """Delete a memory, for GPT"""
        assert ctx.guild
        if ctx.guild.id in self.memory and name in self.memory[ctx.guild.id]:
            async with self.config.guild(ctx.guild).memory() as memory:
                del memory[name]
            del self.memory[ctx.guild.id][name]
            await ctx.tick(message="Memory deleted")
        else:
            await ctx.send("A memory by that name doesn't exist.")
        
    @commands.command(name="setmemory") # type: ignore
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def command_setmemory(self, ctx: commands.Context, name: str, *, content: str):
        """Overwrite a memory, for GPT"""
        assert ctx.guild
        async with self.config.guild(ctx.guild).memory() as memory:
            memory[name] = content
        if ctx.guild.id not in self.memory:
            self.memory[ctx.guild.id] = {}
        self.memory[ctx.guild.id][name] = content
        await ctx.tick(message="Memory set")

    @commands.group(name="gpt", aliases=["gptmemory", "memoryconfig"]) # type: ignore
    @commands.is_owner()
    @commands.guild_only()
    async def memoryconfig(self, ctx: commands.Context):
        """Base command for configuring the GPT Memory cog."""
        pass

    @memoryconfig.command(name="channels")
    async def memoryconfig_channels(self, ctx: commands.Context, mode: Literal["whitelist", "blacklist", "show"], channels: commands.Greedy[discord.TextChannel]):
        """Resets the channels the bot has access to."""
        assert ctx.guild
        if mode == "show":
            mode = await self.config.guild(ctx.guild).channel_mode()
            channels = await self.config.guild(ctx.guild).channels()
        else:
            channels = [c.id for c in channels] # type: ignore
            await self.config.guild(ctx.guild).channel_mode.set(mode)
            await self.config.guild(ctx.guild).channels.set(channels)
        
        await ctx.reply(f"`[channel_mode:]` {mode}\n`[channels]`\n>>> " + "\n".join([f"<#{cid}>" for cid in channels]), mention_author=False)

    # Config

    @memoryconfig.group(name="prompt")
    async def memoryconfig_prompt(self, ctx: commands.Context):
        """View or edit the prompts"""
        pass

    PromptTypes = Literal["recaller", "responder", "memorizer"]

    @memoryconfig.command("model")
    @commands.is_owner()
    async def memoryconfig_model(self, ctx: commands.Context, module: PromptTypes, model: Optional[str]):
        """Views or changes the OpenAI model being used for the recaller, responder, or memorizer."""
        assert ctx.guild
        if module == "recaller":
            model_value = await self.config.guild(ctx.guild).model_recaller()
            model_setter = self.config.guild(ctx.guild).model_recaller
        elif module == "responder":
            model_value = await self.config.guild(ctx.guild).model_responder()
            model_setter = self.config.guild(ctx.guild).model_responder
        elif module == "memorizer":
            model_value = await self.config.guild(ctx.guild).model_memorizer()
            model_setter = self.config.guild(ctx.guild).model_memorizer

        if not model or not model.strip():
            await ctx.reply(f"Current model for the {module} is {model_value}")
        elif model.strip().lower() not in constants.VISION_MODELS:
            await ctx.reply("Invalid model!\nValid models are " + ",".join([f"`{m}`" for m in constants.VISION_MODELS]))
        else:
            await model_setter.set(model.strip().lower())
            await ctx.tick(message="Model changed")

    @memoryconfig.command("effort")
    @commands.is_owner()
    async def memoryconfig_effort(self, ctx: commands.Context, module: PromptTypes, effort: Optional[str]):
        """Views or changes the reasoning effort for the recaller, responder, or memorizer."""
        assert ctx.guild
        if module == "recaller":
            effort_value = await self.config.guild(ctx.guild).effort_recaller()
            effort_setter = self.config.guild(ctx.guild).effort_recaller
        elif module == "responder":
            effort_value = await self.config.guild(ctx.guild).effort_responder()
            effort_setter = self.config.guild(ctx.guild).effort_responder
        elif module == "memorizer":
            effort_value = await self.config.guild(ctx.guild).effort_memorizer()
            effort_setter = self.config.guild(ctx.guild).effort_memorizer

        if not effort or not effort.strip():
            await ctx.reply(f"Current effort for the {module} is {effort_value}")
        elif effort.strip().lower() not in constants.EFFORT_VALUES:
            await ctx.reply("Invalid value!\nValid values are " + ",".join([f"`{m}`" for m in constants.EFFORT_VALUES]))
        else:
            await effort_setter.set(effort.strip().lower())
            await ctx.tick(message="Reasoning effort changed")

    @memoryconfig_prompt.command(name="show", aliases=["view"])
    async def memoryconfig_prompt_show(self, ctx: commands.Context, module: PromptTypes):
        """
        The recaller grabs relevant memories.
        The responder sends the chat message.
        The memorizer edits memories.
        """
        assert ctx.guild
        prompt = ""
        if module == "recaller":
            prompt = await self.config.guild(ctx.guild).prompt_recaller()
        elif module == "responder":
            prompt = await self.config.guild(ctx.guild).prompt_responder()
        elif module == "memorizer":
            prompt = await self.config.guild(ctx.guild).prompt_memorizer()
        
        await ctx.reply(f"`[{module} prompt]`\n>>> {prompt or '*None*'}", mention_author=False)

    @memoryconfig_prompt.command(name="set", aliases=["edit"])
    async def memoryconfig_prompt_set(self, ctx: commands.Context, module: PromptTypes, *, prompt):
        """
        Examples in the default values. Each prompt will require some variables between curly brackets.
        The recaller grabs relevant memories.
        The responder sends the chat message.
        The memorizer edits memories.
        """
        assert ctx.guild
        prompt = prompt.strip()
        if not prompt:
            await ctx.reply("Invalid prompt", mention_author=False)
            return
        
        if module == "recaller":
            await self.config.guild(ctx.guild).prompt_recaller.set(prompt)
        elif module == "responder":
            await self.config.guild(ctx.guild).prompt_responder.set(prompt)
        elif module == "memorizer":
            await self.config.guild(ctx.guild).prompt_memorizer.set(prompt)

        await ctx.reply(f"`[New {module} prompt]`\n>>> {prompt}", mention_author=False)

    @memoryconfig.command(name="allow_memorizer")
    async def memoryconfig_allow_memorizer(self, ctx: commands.Context, value: Optional[bool]):
        """Whether the memorizer will run at all, editing memories."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).allow_memorizer()
        else:
            await self.config.guild(ctx.guild).allow_memorizer.set(value)
        await ctx.reply(f"`[allow_memorizer:]` {value}", mention_author=False)

    @memoryconfig.command(name="memorizer_user_only")
    async def memoryconfig_memorizeR_user_only(self, ctx: commands.Context, value: Optional[bool]):
        """If enabled, only memories of usernames will be passed to the memorizer."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).memorizer_user_only()
        else:
            await self.config.guild(ctx.guild).memorizer_user_only.set(value)
        await ctx.reply(f"`[memorizer_user_only:]` {value}", mention_author=False)

    @memoryconfig.command(name="memorizer_alerts")
    async def memoryconfig_memorizer_alerts(self, ctx: commands.Context, value: Optional[bool]):
        """Whether the memorizer will send a message in chat after editing memories."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).memorizer_alerts()
        else:
            await self.config.guild(ctx.guild).memorizer_alerts.set(value)
        await ctx.reply(f"`[memorizer_alerts:]` {value}", mention_author=False)

    @memoryconfig_prompt.command(name="emotes")
    async def memoryconfig_emotes(self, ctx: commands.Context, *, emotes: Optional[str]):
        """Shows or sets a list of emotes to show the responder."""
        assert ctx.guild
        if not emotes:
            emotes = await self.config.guild(ctx.guild).emotes()
        else:
            emotes = emotes.strip()
            await self.config.guild(ctx.guild).emotes.set(emotes)
        await ctx.reply(f"`[emotes]`\n>>> {emotes}", mention_author=False)

    @memoryconfig.group(name="functions")
    async def memoryconfig_functions(self, _: commands.Context):
        """List or toggle function calls used by the responder."""
        pass

    @memoryconfig_functions.command(name="list")
    async def memoryconfig_functions_list(self, ctx: commands.Context):
        """Shows all functions and whether they are active."""
        assert ctx.guild
        disabled_functions = await self.config.guild(ctx.guild).disabled_functions()
        functions = []
        for tool in get_all_function_calls():
            name = tool.schema.function.name
            s = f"`{name}`: {'disabled' if name in disabled_functions else 'enabled'}"
            for api in tool.apis:
                secret = (await self.bot.get_shared_api_tokens(api[0])).get(api[1])
                if not secret:
                    s += f" (API not set: {api[0]} {api[1]})"
            functions.append(s)
        await ctx.send(">>> " + "\n".join(functions))

    @memoryconfig_functions.command(name="toggle")
    async def memoryconfig_functions_toggle(self, ctx: commands.Context, function_name: str):
        assert ctx.guild
        """Enables or disables a function"""
        all_function_names = [f.schema.function.name for f in get_all_function_calls()]
        if function_name not in all_function_names:
            await ctx.send("Function not found, valid values are: " + ", ".join([f"`{name}`" for name in all_function_names]))
            return
        disabled_functions: list[str] = await self.config.guild(ctx.guild).disabled_functions()
        enabled = function_name not in disabled_functions
        if enabled:
            disabled_functions.append(function_name)
        else:
            disabled_functions.remove(function_name)
        await self.config.guild(ctx.guild).disabled_functions.set(disabled_functions)
        enabled = not enabled
        await ctx.send(f"`{function_name}`: {'enabled' if enabled else 'disabled'}")

    @memoryconfig.group(name="limits")
    async def memoryconfig_limits(self, _: commands.Context):
        """Base command for limits intended as cost-saving measures."""
        pass

    @memoryconfig_limits.command(name="response_tokens")
    async def memoryconfig_response_tokens(self, ctx: commands.Context, value: Optional[int]):
        """Hard limit on the number of tokens the responder will send."""
        assert ctx.guild
        if not value:
            value = await self.config.guild(ctx.guild).response_tokens()
        elif value < 1000 or value > 20000:
            await ctx.reply("Value must be between 1000 and 20000", mention_author=False)
            return
        else:
            await self.config.guild(ctx.guild).response_tokens.set(value)
        await ctx.reply(f"`[response_tokens:]` {value}", mention_author=False)

    @memoryconfig_limits.command(name="backread_tokens")
    async def memoryconfig_backread_tokens(self, ctx: commands.Context, value: Optional[int]):
        """Soft limit on the number of tokens the LLM will read from the chat history."""
        assert ctx.guild
        if not value:
            value = await self.config.guild(ctx.guild).backread_tokens()
        elif value < 100 or value > 10000:
            await ctx.reply("Value must be between 100 and 10000", mention_author=False)
            return
        else:
            await self.config.guild(ctx.guild).backread_tokens.set(value)
        await ctx.reply(f"`[backread_tokens:]` {value}", mention_author=False)

    @memoryconfig_limits.command(name="backread_messages")
    async def memoryconfig_backread_messages(self, ctx: commands.Context, value: Optional[int]):
        """How many messages in chat the recaller and responder will read."""
        assert ctx.guild
        if not value:
            value = await self.config.guild(ctx.guild).backread_messages()
        elif value < 0 or value > 100:
            await ctx.reply("Value must be between 0 and 100", mention_author=False)
            return
        else:
            await self.config.guild(ctx.guild).backread_messages.set(value)
        await ctx.reply(f"`[backread_messages:]` {value}", mention_author=False)

    @memoryconfig_limits.command(name="backread_memorizer")
    async def memoryconfig_backread_memorizer(self, ctx: commands.Context, value: Optional[int]):
        """How many messages in chat the memorizer will read."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).backread_memorizer()
        elif value < 0 or value > 100:
            await ctx.reply("Value must be between 0 and 100", mention_author=False)
            return
        else:
            await self.config.guild(ctx.guild).backread_memorizer.set(value)
        await ctx.reply(f"`[backread_memorizer:]` {value}", mention_author=False)

    @memoryconfig_limits.command(name="max_images")
    async def memoryconfig_max_images(self, ctx: commands.Context, value: Optional[int]):
        """How many images to extract from the whole chat history."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).max_images()
        elif value < 0 or value > 100:
            await ctx.reply("Value must be between 0 and 100", mention_author=False)
            return
        else:
            await self.config.guild(ctx.guild).max_images.set(value)
        await ctx.reply(f"`[max_images:]` {value}", mention_author=False)

    @memoryconfig_limits.command(name="max_images_per_message")
    async def memoryconfig_max_images_per_message(self, ctx: commands.Context, value: Optional[int]):
        """How many images to extract from each message."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).max_images_per_message()
        elif value < 1 or value > 10:
            await ctx.reply("Value must be between 1 and 10", mention_author=False)
            return
        else:
            await self.config.guild(ctx.guild).max_images_per_message.set(value)
        await ctx.reply(f"`[max_images_per_message:]` {value}", mention_author=False)

    @memoryconfig_limits.command(name="max_tool")
    async def memoryconfig_max_tool(self, ctx: commands.Context, value: Optional[int]):
        """Character limit for function calls."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).max_tool()
        elif value < 1000 or value > 20000:
            await ctx.reply("Value must be between 1000 and 20000", mention_author=False)
            return
        else:
            await self.config.guild(ctx.guild).max_tool.set(value)
        await ctx.reply(f"`[max_tool:]` {value}", mention_author=False)

    @memoryconfig_limits.command(name="max_quote")
    async def memoryconfig_max_quote(self, ctx: commands.Context, value: Optional[int]):
        """Character limit for message replies."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).max_quote()
        elif value < 200 or value > 10000:
            await ctx.reply("Value must be between 200 and 10000", mention_author=False)
            return
        else:
            await self.config.guild(ctx.guild).max_quote.set(value)
        await ctx.reply(f"`[max_quote:]` {value}", mention_author=False)

    @memoryconfig_limits.command(name="max_text_file")
    async def memoryconfig_max_text_file(self, ctx: commands.Context, value: Optional[int]):
        """Character limit for text files."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).max_text_file()
        elif value < 2000 or value > 20000:
            await ctx.reply("Value must be between 2000 and 20000", mention_author=False)
            return
        else:
            await self.config.guild(ctx.guild).max_text_file.set(value)
        await ctx.reply(f"`[max_text_file:]` {value}", mention_author=False)

    @memoryconfig_limits.command(name="max_image_resolution")
    async def memoryconfig_max_image_resolution(self, ctx: commands.Context, value: Optional[int]):
        """Images will be resized to this resolution before being sent to OpenAI."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).max_image_resolution()
        elif value < 512 or value > 2048:
            await ctx.reply("Value must be between 512 and 2048", mention_author=False)
            return
        else:
            await self.config.guild(ctx.guild).max_image_resolution.set(value)
        await ctx.reply(f"`[max_image_resolution:]` {value}", mention_author=False)