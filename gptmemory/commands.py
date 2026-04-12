import discord
from typing import Literal, Optional
from difflib import get_close_matches
from functools import reduce
from redbot.core import commands

from gptmemory.base import GptMemoryBase
from gptmemory.utils import chunk_and_send
from gptmemory.constants import EFFORT_VALUES, VISION_MODELS, DISCORD_EPOCH_DATETIME
from gptmemory.functions.base import get_all_function_calls
from gptmemory.views.memory_info import MemoryInfoView


class GptMemoryCommands(GptMemoryBase):

    @commands.command(name="forget")
    async def command_forget(self, ctx: commands.Context):
        """Temporarily makes the bot only read messages past a certain point."""
        assert isinstance(ctx.channel, (discord.TextChannel, discord.Thread))
        await self.config.channel(ctx.channel).start.set(ctx.message.created_at.isoformat())
        await ctx.tick(message="✅")

    @commands.has_permissions(manage_messages=True)
    @commands.command(name="unforget")
    async def command_unforget(self, ctx: commands.Context):
        """Undoes the effect of [p]forget"""
        assert ctx.guild and isinstance(ctx.channel, (discord.TextChannel, discord.Thread))
        await self.config.channel(ctx.channel).start.set(DISCORD_EPOCH_DATETIME.isoformat())
        await ctx.tick(message="✅")
        # remove the previous [p]forget, this is not perfect but it's not important anyway
        if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            limit = await self.config.guild(ctx.guild).backread_messages()
            async for message in ctx.channel.history(limit=limit, before=ctx.message, oldest_first=False):
                if message.content == ctx.message.content.replace("unforget", "forget"):
                    try:
                        await message.delete()
                    except discord.DiscordException:
                        pass
            try:
                await ctx.message.delete()
            except discord.DiscordException:
                pass

    @commands.command(name="mymemory")
    @commands.guild_only()
    async def command_mymemory(self, ctx: commands.Context):
        """View your personal memory in the bot LLM"""
        await self.command_memory(ctx, ctx.author.name)

    @commands.command(name="memory", aliases=["memories"], invoke_without_subcommand=True)
    @commands.guild_only()
    async def command_memory(self, ctx: commands.Context, *, name: discord.Member | str | None):
        """View all memories or a specific memory, for the bot LLM"""
        assert ctx.guild
        if not name:
            if ctx.guild.id in self.memory and self.memory[ctx.guild.id]:
                memories = self.memory[ctx.guild.id].keys()
                user_memories = [memory for memory in memories if any(member.name == memory for member in ctx.guild.members)]
                memories = [memory for memory in memories if memory not in user_memories]
                reply = "`[Memories:]`\n> " + ", ".join(f"`{mem}`" for mem in memories) \
                    + "\n\n`[User memories:]`\n> " + ", ".join(f"`{mem}`" for mem in user_memories)
                return await ctx.send(reply[:2000])
            else:
                return await ctx.send("No memories...")
        if isinstance(name, discord.Member):
            name = name.name
        if ctx.guild.id in self.memory:
            if name not in self.memory[ctx.guild.id]:
                matches = get_close_matches(name, self.memory[ctx.guild.id])
                if matches:
                    name = matches[0]
            if name in self.memory[ctx.guild.id]:
                view = MemoryInfoView(name, self.memory[ctx.guild.id][name])
                view.message = await ctx.send(view=view)
                if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
                    await ctx.message.delete()
        await ctx.send(f"No memory of `{name}`")

    @commands.command(name="deletememory", aliases=["delmemory"]) # type: ignore
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def command_deletememory(self, ctx: commands.Context, *, name: str):
        """Delete an LLM memory"""
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
        """Overwrite an LLM memory"""
        assert ctx.guild
        name = name.replace("`", "")
        if not name:
            return await ctx.send("Invalid name")
        if len(name) > 1000:
            return await ctx.send("Name too long")
        async with self.config.guild(ctx.guild).memory() as memory:
            memory[name] = content
        if ctx.guild.id not in self.memory:
            self.memory[ctx.guild.id] = {}
        self.memory[ctx.guild.id][name] = content
        await ctx.tick(message="Memory set")

    # Config

    @commands.group(name="llm", aliases=["gpt", "gptmemory", "memoryconfig"]) # type: ignore
    @commands.is_owner()
    @commands.guild_only()
    async def memoryconfig(self, _: commands.Context):
        """Base command for configuring the GPT Memory cog."""
        pass

    @memoryconfig.command(name="config", aliases=["settings"])
    async def memoryconfig_config(self, ctx: commands.Context):
        """View all settings"""
        assert ctx.guild
        settings = await self.config.guild(ctx.guild).all()
        functions = []
        for tool in get_all_function_calls():
            name = tool.schema.function.name
            if name in settings["disabled_functions"]:
                continue
            for api in tool.apis:
                secret = (await self.bot.get_shared_api_tokens(api[0])).get(api[1])
                if not secret:
                    break
            else:
                functions.append(name)

        response = ">>> # GptMemory Settings"
        response += "\n`[whitelisted_channels:]` " if settings["channel_mode"] == "whitelist" else "\n`[blacklisted_channels:]` " 
        response += " ".join([f"<#{cid}>" for cid in settings["channels"]])
        if "generate_stable_diffusion" in functions:
            response += "\n`[whitelisted_generation_channels:]` " if settings["generation_channel_mode"] == "whitelist" else "\n`[blacklisted_generation_channels:]` " 
            response += " ".join([f"<#{cid}>" for cid in settings["generation_channels"]])
        response += f"\n`[model_recaller:]` {settings['model_recaller']} `[effort_recaller:]` {settings['effort_recaller']}"
        response += f"\n`[model_responder:]` {settings['model_responder']} `[effort_responder:]` {settings['effort_responder']}"
        response += f"\n`[model_memorizer:]` {settings['model_memorizer']} `[effort_memorizer:]` {settings['effort_memorizer']}"
        response += f"\n`[allow_memorizer:]` {settings['allow_memorizer']} `[memorizer_alerts:]` {settings['memorizer_alerts']} `[memorizer_user_only:]` {settings['memorizer_user_only']}"
        response += f"\n`[functions:]` {' / '.join(functions)}" 
        response += f"\n`[emotes:]` {settings['emotes']}"
        response += "\n## Limits"
        response += f"\n`[response_tokens:]` {settings['response_tokens']} `[backread_tokens:]` {settings['backread_tokens']}"
        response += f"\n`[backread_messages:]` {settings['backread_messages']} `[backread_memorizer:]` {settings['backread_memorizer']}"
        response += f"\n`[max_images:]` {settings['max_images']} `[max_image_resolution:]` {settings['max_image_resolution']}"
        response += f"\n`[max_tool:]` {settings['max_tool']} `[max_tool_depth:]` {settings['max_tool_depth']}"
        response += f"\n`[max_quote:]` {settings['max_quote']} `[max_text_file:]` {settings['max_text_file']}"

        await ctx.send(response)

    @memoryconfig.command(name="logging")
    async def memoryconfig_logging(self, ctx: commands.Context):
        """Toggles logging mode, for the developer."""
        self.extended_logging = not self.extended_logging
        await self.config.extended_logging.set(self.extended_logging)
        await ctx.reply(f"`[logging:]` {'full' if self.extended_logging else 'minimal'}", mention_author=False)
    
    channel_mode = Literal["whitelist", "blacklist", "show"]

    @memoryconfig.command(name="channels")
    async def memoryconfig_channels(self, ctx: commands.Context, mode: channel_mode, channels: commands.Greedy[discord.TextChannel]):
        """Shows or sets the channels the bot has access to."""
        assert ctx.guild
        if mode == "show":
            mode = await self.config.guild(ctx.guild).channel_mode()
            channel_ids = await self.config.guild(ctx.guild).channels()
        else:
            channel_ids = [c.id for c in channels] # type: ignore
            await self.config.guild(ctx.guild).channel_mode.set(mode)
            await self.config.guild(ctx.guild).channels.set(channel_ids)
        
        await ctx.reply(f"`[channel_mode:]` {mode}\n`[channels]`\n>>> " + "\n".join([f"<#{cid}>" for cid in channel_ids]), mention_author=False)

    @memoryconfig.command(name="generation_channels")
    async def memoryconfig_generation_channels(self, ctx: commands.Context, mode: channel_mode, channels: commands.Greedy[discord.TextChannel]):
        """Shows or sets the channels the stable diffusion generation tool has access to."""
        assert ctx.guild
        if mode == "show":
            mode = await self.config.guild(ctx.guild).generation_channel_mode()
            channel_ids = await self.config.guild(ctx.guild).generation_channels()
        else:
            channel_ids = [c.id for c in channels]
            await self.config.guild(ctx.guild).generation_channel_mode.set(mode)
            await self.config.guild(ctx.guild).generation_channels.set(channel_ids)
        
        await ctx.reply(f"`[generation_channel_mode:]` {mode}\n`[generation_channels]`\n>>> " + "\n".join([f"<#{cid}>" for cid in channel_ids]), mention_author=False)

    @memoryconfig.group(name="prompt")
    async def memoryconfig_prompt(self, ctx: commands.Context):
        """View or edit the prompts"""
        pass

    PromptTypes = Literal["recaller", "responder", "memorizer"]
    AllPromptTypes = PromptTypes | Literal["autoresponder"]

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
        elif "/" not in model and model.strip().lower() not in VISION_MODELS:
            await ctx.reply("Invalid model!\nValid models are " + ",".join([f"`{m}`" for m in VISION_MODELS]))
        else:
            await model_setter.set(model.strip().lower())
            if "/" in model:
                await ctx.reply("Model changed. Note that this model will be used through OpenRouter, and things may break unexpectedly.")
            else:
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
        elif effort.strip().lower() not in EFFORT_VALUES:
            await ctx.reply("Invalid value!\nValid values are " + ",".join([f"`{m}`" for m in EFFORT_VALUES]))
        else:
            await effort_setter.set(effort.strip().lower())
            await ctx.tick(message="Reasoning effort changed")

    @memoryconfig_prompt.command(name="show", aliases=["view"])
    async def memoryconfig_prompt_show(self, ctx: commands.Context, module: AllPromptTypes):
        """
        The recaller grabs relevant memories.
        The responder sends the chat message.
        The autoresponder sends random chat messages.
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
        elif module == "autoresponder":
            prompt = await self.config.guild(ctx.guild).prompt_autoresponder()
        
        await chunk_and_send(ctx, f"`[{module} prompt]`\n```\n{prompt or '*None*'}\n```", False)

    @memoryconfig_prompt.command(name="set", aliases=["edit"])
    async def memoryconfig_prompt_set(self, ctx: commands.Context, module: AllPromptTypes, *, prompt):
        """
        Examples in the default values. Each prompt will require some variables between curly brackets.
        The recaller grabs relevant memories.
        The responder sends the chat message.
        The autoresponder sends random chat messages.
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
        elif module == "autoresponder":
            await self.config.guild(ctx.guild).prompt_autoresponder.set(prompt)

        await ctx.tick()

    @memoryconfig_prompt.command(name="key", aliases=["keys"])
    async def memoryconfig_keys(self, ctx: commands.Context, key: Optional[str], *, value: Optional[str]):
        """Shows or sets a {key} to act as a shorthand in the responder prompt."""
        assert ctx.guild
        all_keys = await self.config.guild(ctx.guild).prompt_keys()
        if not key:
            content = f"`[prompt_keys:]` ```\n" + "\n".join([f"{{{key}}}" for key in all_keys.keys()]) + "\n```"
        elif not value:
            if key in all_keys:
                content = f"`[{key}:]` ```\n{all_keys[key].replace('```', '`')}```"
            else:
                content = "Key not found. You can use this same command to set a value for it or clear it."
        elif value.lower() in ("delete", "clear", "none", "empty", "erase"):
            if key in all_keys:
                del all_keys[key]
                await self.config.guild(ctx.guild).prompt_keys.set(all_keys)
            return await ctx.tick()
        else:
            all_keys[key] = value
            await self.config.guild(ctx.guild).prompt_keys.set(all_keys)
            return await ctx.tick()
        await ctx.reply(content, mention_author=False)

    @memoryconfig.command(name="allow_memorizer", aliases=["enable_memorizer"])
    async def memoryconfig_allow_memorizer(self, ctx: commands.Context, value: Optional[bool]):
        """Whether the memorizer will run at all, editing memories."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).allow_memorizer()
        else:
            await self.config.guild(ctx.guild).allow_memorizer.set(value)
        await ctx.reply(f"`[allow_memorizer:]` {value}", mention_author=False)

    @memoryconfig.command(name="memorizer_user_only")
    async def memoryconfig_memorizer_user_only(self, ctx: commands.Context, value: Optional[bool]):
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

    @memoryconfig.command(name="autoresponder_chance")
    async def memoryconfig_autoresponder_chance(self, ctx: commands.Context, percent: Optional[float]):
        """The chance that the autoresponder will trigger, from 0.0 to 100.0"""
        assert ctx.guild
        if percent is None:
            percent = await self.config.guild(ctx.guild).autoresponder_chance()
        elif percent < 0 or percent > 100:
            await ctx.reply("Value must range from 0.0 to 100.0", mention_author=False)
            return
        else:
            percent /= 100
            await self.config.guild(ctx.guild).autoresponder_chance.set(percent)
        assert percent
        await ctx.reply(f"`[autoresponder_chance:]` {percent*100:.2f}%", mention_author=False)

    @memoryconfig.command(name="autoresponder_cooldown")
    async def memoryconfig_autoresponder_cooldown(self, ctx: commands.Context, minutes: Optional[int]):
        """The minimum time between 2 autoresponder triggers in a single channel."""
        assert ctx.guild
        if minutes is None:
            minutes = await self.config.guild(ctx.guild).autoresponder_cooldown_minutes()
        elif minutes < 0:
            await ctx.reply("Value must not be negative.", mention_author=False)
        else:
            await self.config.guild(ctx.guild).autoresponder_cooldown_minutes.set(minutes)
        assert minutes
        await ctx.reply(f"`[autoresponder_cooldown:]` {minutes} minutes", mention_author=False)

    @memoryconfig.command(name="timeout")
    async def memoryconfig_timeout(self, ctx: commands.Context, value: Optional[int]):
        """
        Sets how long a response can take before it's cancelled
        """
        if not value:
            value = await self.config.response_timeout()
        elif value < 10 or value > 3600:
            await ctx.reply("Value must be between 10 and 3600", mention_author=False)
            return
        else:
            await self.config.response_timeout.set(value)
        await ctx.reply(f"`[timeout:]` {value}", mention_author=False)
    
    @memoryconfig.command(name="slow_timer")
    async def memoryconfig_slow_timer(self, ctx: commands.Context, value: Optional[int]):
        """
        Sets how long a response can take before reacting with slow_emoji
        """
        if not value:
            value = await self.config.slow_timer()
        elif value < 5 or value > 600:
            await ctx.reply("Value must be between 5 and 600", mention_author=False)
            return
        else:
            await self.config.slow_timer.set(value)
        await ctx.reply(f"`[slow_timer:]` {value}", mention_author=False)
    
    @memoryconfig.command(name="slow_emoji")
    async def memoryconfig_slow_emoji(self, ctx: commands.Context, emoji: discord.Emoji):
        """
        Sets an emoji to react when the LLM takes too long.
        """
        try:
            await ctx.react_quietly(emoji)
        except (discord.NotFound, discord.Forbidden):
            await ctx.reply("I don't have access to that emoji. I must be in the same server to use it.")
        else:
            await self.config.slow_emoji.set(str(emoji))
            await ctx.tick()

    @memoryconfig.command(name="noresponse_emoji")
    async def memoryconfig_noresponse_emoji(self, ctx: commands.Context, emoji: discord.Emoji):
        """
        Sets an emoji for when the LLM doesn't respond.
        """
        try:
            await ctx.react_quietly(emoji)
        except (discord.NotFound, discord.Forbidden):
            await ctx.reply("I don't have access to that emoji. I must be in the same server to use it.")
        else:
            await self.config.noresponse_emoji.set(str(emoji))
            await ctx.tick()

    @memoryconfig.command(name="blocked_emoji")
    async def memoryconfig_blocked_emoji(self, ctx: commands.Context, emoji: discord.Emoji):
        """
        Sets an emoji for when the LLM response gets blocked.
        """
        try:
            await ctx.react_quietly(emoji)
        except (discord.NotFound, discord.Forbidden):
            await ctx.reply("I don't have access to that emoji. I must be in the same server to use it.")
        else:
            await self.config.blocked_emoji.set(str(emoji))
            await ctx.tick()

    @memoryconfig.group(name="functions", aliases=["function", "tools", "tool"])
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

    @memoryconfig_functions.command(name="setting", aliases=["settings"])
    async def memoryconfig_functions_setting(self, ctx: commands.Context, key: Optional[str], *, value: str = ""):
        """
        Sets a tool-specific key-value setting.
        """
        setting_dict = reduce(lambda a, b: a | b, [func.settings for func in get_all_function_calls()])
        setting_values = await self.config.tool_settings()
        if not key:
            lines = [f"`{key}`: `{setting_values.get(key, default or '(empty)')}`" for key, default in setting_dict.items()]
            return await ctx.send(">>> " + "\n".join(lines))
        if key not in setting_dict:
            return await ctx.send("Invalid setting name. Options are: " + ", ".join([f"`{k}`" for k in setting_dict]))
        value = value.strip(" `\n")
        if "emoji" in key or "emote" in key:
            try:
                await ctx.react_quietly(value)
            except (discord.NotFound, discord.Forbidden):
                return await ctx.reply("Invalid emoji. Note that I must be in the same server as the emoji to use it.")
        async with self.config.tool_settings() as settings:
            settings[key] = value
        await ctx.tick()

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

    @memoryconfig_limits.command(name="max_depth", aliases=["max_tool_depth"])
    async def memoryconfig_max_tool_depth(self, ctx: commands.Context, value: Optional[int]):
        """How many tools the AI can use one after the other."""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).max_tool_depth()
        elif value < 1 or value > 10:
            await ctx.reply("Value must be between 1 and 10", mention_author=False)
            return
        else:
            await self.config.guild(ctx.guild).max_tool_depth.set(value)
        await ctx.reply(f"`[max_tool_depth:]` {value}", mention_author=False)

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
