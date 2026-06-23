import discord
from typing import Literal, Optional
from functools import reduce
from redbot.core import commands

from agent.base import AgentCogBase
from agent.config import ConfigField
from agent.constants import EFFORT_VALUES, VISION_MODELS
from agent.tools.base import get_all_tools


class AgentCogConfigCommands(AgentCogBase):

    @commands.group(name="agent", aliases=["llm", "gpt"]) # type: ignore
    @commands.is_owner()
    @commands.guild_only()
    async def agentconfig(self, _: commands.Context):
        """Base command for configuring the GPT Memory cog."""
        pass


    @agentconfig.command(name="config", aliases=["settings"])
    async def agentconfig_config(self, ctx: commands.Context):
        """View all settings"""
        config = self.config[ctx.guild]
        functions = []
        for tool in get_all_tools():
            name = tool.display_name
            if name not in config.enabled_functions.value:
                continue
            for api in tool.apis:
                secret = (await self.bot.get_shared_api_tokens(api[0])).get(api[1])
                if not secret:
                    break
            else:
                functions.append(name)

        response = ">>> # Agent Cog Settings"
        response += "\n`[whitelisted_channels:]` " if config.channel_mode.value == "whitelist" else "\n`[blacklisted_channels:]` " 
        response += " ".join([f"<#{cid}>" for cid in config.channels.value])
        response += "\n`[whitelisted_auto_channels:]` " if config.auto_channel_mode.value == "whitelist" else "\n`[blacklisted_auto_channels:]` " 
        response += " ".join([f"<#{cid}>" for cid in config.auto_channels.value])
        if "generate_stable_diffusion" in functions:
            response += "\n`[whitelisted_generation_channels:]` " if config.generation_channel_mode.value == "whitelist" else "\n`[blacklisted_generation_channels:]` " 
            response += " ".join([f"<#{cid}>" for cid in config.generation_channels.value])
        response += f"\n`[model_recaller:]` {config.model_recaller.value} `[effort_recaller:]` {config.effort_recaller.value}"
        response += f"\n`[model_responder:]` {config.model_responder.value} `[effort_responder:]` {config.effort_responder.value}"
        response += f"\n`[model_memorizer:]` {config.model_memorizer.value} `[effort_memorizer:]` {config.effort_memorizer.value}"
        response += f"\n`[allow_memorizer:]` {config.allow_memorizer.value} `[memorizer_alerts:]` {config.memorizer_alerts.value} `[memorizer_user_only:]` {config.memorizer_user_only.value}"
        response += f"\n`[tools:]` {' / '.join(functions)}" 
        response += "\n## Limits"
        response += f"\n`[response_tokens:]` {config.response_tokens.value} `[backread_tokens:]` {config.backread_tokens.value}"
        response += f"\n`[backread_messages:]` {config.backread_messages.value} `[backread_short:]` {config.backread_short.value}"
        response += f"\n`[max_images:]` {config.max_images.value} `[max_image_resolution:]` {config.max_image_resolution.value}"
        response += f"\n`[max_tool:]` {config.max_tool.value} `[max_tool_depth:]` {config.max_tool_depth.value}"
        response += f"\n`[max_quote:]` {config.max_quote.value} `[max_text_file:]` {config.max_text_file.value}"

        await ctx.send(response)


    @staticmethod
    async def bool_config_command(ctx: commands.Context, field: ConfigField[bool], value: bool | None):
        if value is None:
            value = field.value
        else:
            await field.set(value)
        await ctx.reply(f"`[{field.name}:]` {value}", mention_author=False)

    @staticmethod
    async def percent_config_command(ctx: commands.Context, field: ConfigField[float], percent: float | None):
        if percent is None:
            percent = field.value
        elif percent < 0 or percent > 100:
            await ctx.reply("Value must range from 0.0 to 100.0", mention_author=False)
            return
        else:
            percent /= 100
            await field.set(percent)
        await ctx.reply(f"`[{field.name}:]` {percent*100:.2f}%", mention_author=False)

    @staticmethod
    async def integer_config_command(ctx: commands.Context, field: ConfigField[int], min: int | None, max: int | None, value: int | None, unit: str = ""):
        if value is None:
            value = field.value
        elif max is not None and value > max or min is not None and value < min:
            if max is not None and min is not None:
                message = f"Value must range between {min} and {max}."
            elif max is not None:
                message = f"Value must not be greater than {max}."
            else:
                message = f"Value must be {min} or greater."
            return await ctx.reply(message, mention_author=False)
        else:
            await field.set(value)
        await ctx.reply(f"`[{field.name}:]` {value} {unit}", mention_author=False)

    @staticmethod
    async def emoji_config_command(ctx: commands.Context, field: ConfigField[str], emote: discord.Emoji):
        try:
            await ctx.react_quietly(emote)
        except (discord.NotFound, discord.Forbidden):
            await ctx.reply("I don't have access to that emote. I must be in the same server to use it.")
        else:
            await field.set(str(emote))
            await ctx.tick()

    @staticmethod
    async def channels_config_command(ctx: commands.Context, values_field: ConfigField[list[int]], mode_field: ConfigField[str], values: set[int] | None, mode: str | None):
        if mode in ("whitelist", "blacklist"):
            await mode_field.set(mode)
        if values is not None:
            await values_field.set(list(values))
        await ctx.reply(f"`[{mode_field.name}:]` {mode_field.value}\n`[{values_field.name}]`\n>>> " + "\n".join([f"<#{cid}>" for cid in values_field.value]), mention_author=False)



    @agentconfig.command(name="logging", aliases=["extended_logging"])
    async def agentconfig_logging(self, ctx: commands.Context, value: Optional[bool]):
        """Toggles logging mode, for the developer."""
        await self.bool_config_command(ctx, self.config.extended_logging, value)
    
    @agentconfig.command(name="allow_memorizer", aliases=["enable_memorizer"])
    async def agentconfig_allow_memorizer(self, ctx: commands.Context, value: Optional[bool]):
        """Whether the memorizer will run at all, editing memories."""
        await self.bool_config_command(ctx, self.config[ctx.guild].allow_memorizer, value)

    @agentconfig.command(name="memorizer_user_only")
    async def agentconfig_memorizer_user_only(self, ctx: commands.Context, value: Optional[bool]):
        """If enabled, only memories of usernames will be passed to the memorizer."""
        await self.bool_config_command(ctx, self.config[ctx.guild].memorizer_user_only, value)

    @agentconfig.command(name="memorizer_alerts")
    async def agentconfig_memorizer_alerts(self, ctx: commands.Context, value: Optional[bool]):
        """Whether the memorizer will send a message in chat after editing memories."""
        await self.bool_config_command(ctx, self.config[ctx.guild].memorizer_alerts, value)

    @agentconfig.command(name="autoresponder_chance")
    async def agentconfig_autoresponder_chance(self, ctx: commands.Context, percent: Optional[float]):
        """The chance that the autoresponder will trigger, from 0.0 to 100.0"""
        await self.percent_config_command(ctx, self.config[ctx.guild].autoresponder_chance, percent)

    @agentconfig.command(name="autoreacter_chance")
    async def agentconfig_autoreacter_chance(self, ctx: commands.Context, percent: Optional[float]):
        """The chance that the autoreacter will trigger, from 0.0 to 100.0"""
        await self.percent_config_command(ctx, self.config[ctx.guild].autoreacter_chance, percent)

    @agentconfig.command(name="autoreacter_chance_images")
    async def agentconfig_autoreacter_chance_images(self, ctx: commands.Context, percent: Optional[float]):
        """The chance that the autoreacter will trigger on an image attachment, from 0.0 to 100.0"""
        await self.percent_config_command(ctx, self.config[ctx.guild].autoreacter_chance_images, percent)

    @agentconfig.command(name="autoresponder_cooldown", aliases=["autoresponder_cooldown_minutes"])
    async def agentconfig_autoresponder_cooldown(self, ctx: commands.Context, minutes: Optional[int]):
        """The minimum time between 2 autoresponder triggers in a single channel."""
        await self.integer_config_command(ctx, self.config[ctx.guild].autoresponder_cooldown_minutes, 0, None, minutes)

    @agentconfig.command(name="autoreacter_cooldown", aliases=["autoreacter_cooldown_minutes"])
    async def agentconfig_autoreacter_cooldown(self, ctx: commands.Context, minutes: Optional[int]):
        """The minimum time between 2 autoreacter triggers in a single channel."""
        await self.integer_config_command(ctx, self.config[ctx.guild].autoreacter_cooldown_minutes, 0, None, minutes)

    @agentconfig.command(name="timeout")
    async def agentconfig_timeout(self, ctx: commands.Context, value: Optional[int]):
        """Sets how long a response can take before it's cancelled"""
        await self.integer_config_command(ctx, self.config.response_timeout, 10, 3600, value, "seconds")
    
    @agentconfig.command(name="slow_timer")
    async def agentconfig_slow_timer(self, ctx: commands.Context, value: Optional[int]):
        """Sets how long a response can take before reacting with slow_emoji"""
        await self.integer_config_command(ctx, self.config.slow_timer, 10, 3600, value, "seconds")
    
    @agentconfig.command(name="slow_emoji")
    async def agentconfig_slow_emoji(self, ctx: commands.Context, emoji: discord.Emoji):
        """Sets an emoji to react when the LLM takes too long."""
        await self.emoji_config_command(ctx, self.config.slow_emoji, emoji)

    @agentconfig.command(name="noresponse_emoji")
    async def agentconfig_noresponse_emoji(self, ctx: commands.Context, emoji: discord.Emoji):
        """Sets an emoji for when the LLM doesn't respond."""
        await self.emoji_config_command(ctx, self.config.noresponse_emoji, emoji)

    @agentconfig.command(name="blocked_emoji")
    async def agentconfig_blocked_emoji(self, ctx: commands.Context, emoji: discord.Emoji):
        """Sets an emoji for when the LLM response gets blocked."""
        await self.emoji_config_command(ctx, self.config.blocked_emoji, emoji)


    @agentconfig.group(name="limits")
    async def agentconfig_limits(self, _: commands.Context):
        """Base command for limits intended as cost-saving measures."""
        pass

    @agentconfig_limits.command(name="response_tokens")
    async def agentconfig_response_tokens(self, ctx: commands.Context, value: Optional[int]):
        """Hard limit on the number of tokens the responder will send."""
        await self.integer_config_command(ctx, self.config[ctx.guild].response_tokens, 500, 20000, value)

    @agentconfig_limits.command(name="backread_tokens")
    async def agentconfig_backread_tokens(self, ctx: commands.Context, value: Optional[int]):
        """Soft limit on the number of tokens the LLM will read from the chat history."""
        await self.integer_config_command(ctx, self.config[ctx.guild].backread_tokens, 500, 20000, value)

    @agentconfig_limits.command(name="backread_messages")
    async def agentconfig_backread_messages(self, ctx: commands.Context, value: Optional[int]):
        """How many messages in chat the recaller and responder will read."""
        await self.integer_config_command(ctx, self.config[ctx.guild].backread_messages, 0, 100, value)

    @agentconfig_limits.command(name="backread_short", aliases=["backread_messages_short"])
    async def agentconfig_backread_short(self, ctx: commands.Context, value: Optional[int]):
        """How many messages in chat will read for shorter contexts (memorizer, autoreacter)."""
        await self.integer_config_command(ctx, self.config[ctx.guild].backread_short, 0, 100, value, "messages")

    @agentconfig_limits.command(name="max_images")
    async def agentconfig_max_images(self, ctx: commands.Context, value: Optional[int]):
        """How many images to send to the LLM in full with each response; the rest will be captioned and stored instead."""
        await self.integer_config_command(ctx, self.config[ctx.guild].max_images, 0, 100, value)

    @agentconfig_limits.command(name="max_depth", aliases=["max_tool_depth"])
    async def agentconfig_max_tool_depth(self, ctx: commands.Context, value: Optional[int]):
        """How many tools the AI can use one after the other. Each consecutive tool call is more expensive than the last."""
        await self.integer_config_command(ctx, self.config[ctx.guild].max_tool_depth, 1, 10, value, "requests")

    @agentconfig_limits.command(name="max_tool", aliases=["max_tool_characters"])
    async def agentconfig_max_tool(self, ctx: commands.Context, value: Optional[int]):
        """Character limit for function call results."""
        await self.integer_config_command(ctx, self.config[ctx.guild].max_tool, 1000, 20000, value, "characters")

    @agentconfig_limits.command(name="max_text_file")
    async def agentconfig_max_text_file(self, ctx: commands.Context, value: Optional[int]):
        """Character limit for text files."""
        await self.integer_config_command(ctx, self.config[ctx.guild].max_text_file, 1000, 20000, value, "characters")

    @agentconfig_limits.command(name="max_quote", aliases=["max_quote_characters"])
    async def agentconfig_max_quote(self, ctx: commands.Context, value: Optional[int]):
        """Character limit for message replies."""
        await self.integer_config_command(ctx, self.config[ctx.guild].max_quote, 100, 10000, value, "characters")

    @agentconfig_limits.command(name="max_image_resolution", aliases=["max_resolution"])
    async def agentconfig_max_image_resolution(self, ctx: commands.Context, value: Optional[int]):
        """Images will be resized to this resolution before being sent to the LLM."""
        await self.integer_config_command(ctx, self.config[ctx.guild].max_image_resolution, 512, 2048, value, "on each side")

    @agentconfig_limits.command(name="max_caption_resolution", aliases=["max_thumbnail_resolution"])
    async def agentconfig_max_caption_resolution(self, ctx: commands.Context, value: Optional[int]):
        """Images will be resized to this resolution before being sent for captioning."""
        await self.integer_config_command(ctx, self.config[ctx.guild].max_caption_resolution, 128, 1024, value, "on each side")



    ChannelMode = Literal["whitelist", "blacklist"]


    @agentconfig.group(name="channels")
    async def agentconfig_channels(self, ctx: commands.Context):
        """Shows or sets the channels the LLM has access to."""

    @agentconfig_channels.command("list", aliases=["show", "view"])
    async def agentconfig_channels_list(self, ctx: commands.Context):
        """Shows the LLM whitelist or blacklist depending on the current setting."""
        config = self.config[ctx.guild]
        await self.channels_config_command(ctx, config.channels, config.channel_mode, None, None)

    @agentconfig_channels.command("set")
    async def agentconfig_channels_set(self, ctx: commands.Context, mode: ChannelMode, channels: commands.Greedy[discord.TextChannel | discord.Thread]):
        """Sets a new whitelist or blacklist for LLM channels"""
        config = self.config[ctx.guild]
        await self.channels_config_command(ctx, config.channels, config.channel_mode, set(c.id for c in channels), mode)

    @agentconfig_channels.command("add")
    async def agentconfig_channels_remove(self, ctx: commands.Context, channels: commands.Greedy[discord.TextChannel | discord.Thread]):
        """Adds channels to the LLM whitelist or blacklist depending on the current setting."""
        config = self.config[ctx.guild]
        channel_ids = set([c.id for c in channels] + config.channels.value)
        await self.channels_config_command(ctx, config.channels, config.channel_mode, channel_ids, None)

    @agentconfig_channels.command("remove")
    async def agentconfig_channels_add(self, ctx: commands.Context, channels: commands.Greedy[discord.TextChannel | discord.Thread]):
        """Removes channels from the LLM whitelist or blacklist depending on the current setting."""
        config = self.config[ctx.guild]
        channel_ids = set(config.channels.value) - set(c.id for c in channels)
        await self.channels_config_command(ctx, config.channels, config.channel_mode, channel_ids, None)


    @agentconfig.group(name="generation_channels")
    async def agentconfig_generation_channels(self, ctx: commands.Context):
        """Shows or sets the channels the LLM can generate images in."""

    @agentconfig_generation_channels.command("list", aliases=["show", "view"])
    async def agentconfig_generation_channels_list(self, ctx: commands.Context):
        """Shows the image generation channel whitelist or blacklist of depending on the current setting."""
        config = self.config[ctx.guild]
        await self.channels_config_command(ctx, config.generation_channels, config.generation_channel_mode, None, None)

    @agentconfig_generation_channels.command("set")
    async def agentconfig_generation_channels_set(self, ctx: commands.Context, mode: ChannelMode, channels: commands.Greedy[discord.TextChannel | discord.Thread]):
        """Sets a new whitelist or blacklist for image generation channels"""
        config = self.config[ctx.guild]
        await self.channels_config_command(ctx, config.generation_channels, config.generation_channel_mode, set(c.id for c in channels), mode)

    @agentconfig_generation_channels.command("add")
    async def agentconfig_generation_channels_remove(self, ctx: commands.Context, channels: commands.Greedy[discord.TextChannel | discord.Thread]):
        """Adds channels to the image generation whitelist or blacklist depending on the current setting."""
        config = self.config[ctx.guild]
        channel_ids = set([c.id for c in channels] + config.generation_channels.value)
        await self.channels_config_command(ctx, config.generation_channels, config.generation_channel_mode, channel_ids, None)

    @agentconfig_generation_channels.command("remove")
    async def agentconfig_generation_channels_add(self, ctx: commands.Context, channels: commands.Greedy[discord.TextChannel | discord.Thread]):
        """Removes channels from the image generation whitelist or blacklist depending on the current setting."""
        config = self.config[ctx.guild]
        channel_ids = set(config.generation_channels.value) - set(c.id for c in channels)
        await self.channels_config_command(ctx, config.generation_channels, config.generation_channel_mode, channel_ids, None)


    @agentconfig.group(name="auto_channels")
    async def agentconfig_auto_channels(self, ctx: commands.Context):
        """Shows or sets the channels the LLM can respond automatically in."""

    @agentconfig_auto_channels.command("list", aliases=["show", "view"])
    async def agentconfig_auto_channels_list(self, ctx: commands.Context):
        """Shows the autoresponse channel whitelist or blacklist of depending on the current setting."""
        config = self.config[ctx.guild]
        await self.channels_config_command(ctx, config.auto_channels, config.auto_channel_mode, None, None)

    @agentconfig_auto_channels.command("set")
    async def agentconfig_auto_channels_set(self, ctx: commands.Context, mode: ChannelMode, channels: commands.Greedy[discord.TextChannel | discord.Thread]):
        """Sets a new whitelist or blacklist for autoresponse channels"""
        config = self.config[ctx.guild]
        await self.channels_config_command(ctx, config.auto_channels, config.auto_channel_mode, set(c.id for c in channels), mode)

    @agentconfig_auto_channels.command("add")
    async def agentconfig_auto_channels_remove(self, ctx: commands.Context, channels: commands.Greedy[discord.TextChannel | discord.Thread]):
        """Adds channels to the autoresponse whitelist or blacklist depending on the current setting."""
        config = self.config[ctx.guild]
        channel_ids = set([c.id for c in channels] + config.auto_channels.value)
        await self.channels_config_command(ctx, config.auto_channels, config.auto_channel_mode, channel_ids, None)

    @agentconfig_auto_channels.command("remove")
    async def agentconfig_auto_channels_add(self, ctx: commands.Context, channels: commands.Greedy[discord.TextChannel | discord.Thread]):
        """Removes channels from the autoresponse whitelist or blacklist depending on the current setting."""
        config = self.config[ctx.guild]
        channel_ids = set(config.auto_channels.value) - set(c.id for c in channels)
        await self.channels_config_command(ctx, config.auto_channels, config.auto_channel_mode, channel_ids, None)



    @agentconfig.group(name="tool", aliases=["function", "functions", "tools"])
    async def agentconfig_functions(self, _: commands.Context):
        """List or toggle function calls used by the responder."""
        pass

    @agentconfig_functions.command(name="list")
    async def agentconfig_functions_list(self, ctx: commands.Context):
        """Shows all functions and whether they are active."""
        enabled_functions = self.config[ctx.guild].enabled_functions.value
        functions = []
        for tool in get_all_tools():
            name = tool.display_name
            s = f"`{name}`: {'enabled' if name in enabled_functions else 'disabled'}"
            for api in tool.apis:
                secret = (await self.bot.get_shared_api_tokens(api[0])).get(api[1])
                if not secret:
                    s += f" (API not set: {api[0]} {api[1]})"
            functions.append(s)
        await ctx.send(">>> " + "\n".join(functions))

    @agentconfig_functions.command(name="enable")
    async def agentconfig_functions_enable(self, ctx: commands.Context, tool_name: str):
        """Enables a tool call"""
        all_tool_names = [t.display_name for t in get_all_tools()]
        if tool_name not in all_tool_names:
            await ctx.send("Function not found, valid values are: " + ", ".join([f"`{name}`" for name in all_tool_names]))
            return
        enabled_tools = self.config[ctx.guild].enabled_functions
        if tool_name not in enabled_tools.value:
            enabled_tools.value.append(tool_name)
            await enabled_tools.save()
        await ctx.send(f"`{tool_name}`: enabled")

    @agentconfig_functions.command(name="disable")
    async def agentconfig_functions_disable(self, ctx: commands.Context, tool_name: str):
        """Enables a tool call"""
        all_tool_names = [t.display_name for t in get_all_tools()]
        if tool_name not in all_tool_names:
            await ctx.send("Function not found, valid values are: " + ", ".join([f"`{name}`" for name in all_tool_names]))
            return
        enabled_tools = self.config[ctx.guild].enabled_functions
        if tool_name in enabled_tools.value:
            enabled_tools.value.remove(tool_name)
            await enabled_tools.save()
        await ctx.send(f"`{tool_name}`: disabled")

    @agentconfig_functions.command(name="setting", aliases=["settings"])
    async def agentconfig_functions_setting(self, ctx: commands.Context, key: Optional[str], *, value: str = ""):
        """Sets a tool-specific key-value setting."""
        setting_dict = reduce(lambda a, b: a | b, [func.settings for func in get_all_tools()])
        setting_values = self.config.tool_settings.value
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
        setting_values[key] = value
        await self.config.tool_settings.save()
        await ctx.tick()



    ModelPromptTypes = Literal["recaller", "responder", "memorizer", "captioner", "autoreacter"]

    @agentconfig.command("model")
    @commands.is_owner()
    async def agentconfig_model(self, ctx: commands.Context, module: ModelPromptTypes, model: Optional[str]):
        """Views or changes the LLM model being used by different modules of this cog."""
        config = self.config[ctx.guild]
        fields = {
            "recaller":    config.model_recaller,
            "responder":   config.model_responder,
            "memorizer":   config.model_memorizer,
            "captioner":   config.model_captioner,
            "autoreacter": config.model_autoreacter,
        }
        if not model or not model.strip():
            await ctx.reply(f"Current model for the {module} is {fields[module].value}")
        elif "/" not in model and "$" not in model and model.strip().lower() not in VISION_MODELS:
            await ctx.reply("Invalid model!\nValid models are " + ",".join([f"`{m}`" for m in VISION_MODELS]))
        else:
            await fields[module].set(model.strip().lower())
            if "$" in model:
                await ctx.reply("Model changed. Note that this model will be used through OpenWebui, and things may break unexpectedly.")
            elif "/" in model:
                await ctx.reply("Model changed. Note that this model will be used through OpenRouter, and things may break unexpectedly.")
            else:
                await ctx.tick(message="Model changed")

    EffortPromptTypes = Literal["recaller", "responder", "memorizer"]

    @agentconfig.command("effort")
    @commands.is_owner()
    async def agentconfig_effort(self, ctx: commands.Context, module: EffortPromptTypes, effort: Optional[str]):
        """Views or changes the LLM reasoning effort used by different modules of this cog."""
        config = self.config[ctx.guild]
        fields = {
            "recaller":  config.effort_recaller,
            "responder": config.effort_responder,
            "memorizer": config.effort_memorizer,
        }
        if not effort or not effort.strip():
            await ctx.reply(f"Current effort for the {module} is {fields[module].value}")
        elif effort.strip().lower() not in EFFORT_VALUES:
            await ctx.reply("Invalid value!\nValid values are " + ",".join([f"`{m}`" for m in EFFORT_VALUES]))
        else:
            await fields[module].set(effort.strip().lower())
            await ctx.tick(message="Reasoning effort changed")

    @agentconfig.command("status")
    @commands.is_owner()
    async def agentconfig_status(self, ctx: commands.Context, *, status: Optional[str]):
        """Little feature to preset a status on bot startup"""
        if not status:
            await self.config.status.set("")
            await self.bot.change_presence(activity=None)
            await ctx.reply("The bot's status will not be set on startup.")
        else:
            status = status[:128].replace("\n", " ")
            await self.config.status.set(status)
            await self.bot.change_presence(activity=discord.CustomActivity(name=status))
            await ctx.reply(f"The bot's status will be set to `{status}` on startup.")