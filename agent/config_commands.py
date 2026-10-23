import logging
from functools import reduce
from typing import Literal, Optional

import discord
from redbot.core import commands

from agent.base import AgentCogBase
from agent.utils import is_valid_openrouter_model
from agent.config_base import ConfigField
from agent.constants import EFFORT_VALUES, VISION_MODELS
from agent.tools.base import get_all_tools

log = logging.getLogger("agent.utils")


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
        assert ctx.guild
        config = self.config[ctx.guild]
 
        tools = []
        imagegen = False
        tool_settings = []
        for tool in get_all_tools():
            name = tool.display_name
            if name not in config.enabled_functions.value:
                continue
            for api in tool.apis:
                secret = (await self.bot.get_shared_api_tokens(api[0])).get(api[1])
                if not secret:
                    break
            else:
                tools.append(name)
                tool_settings.extend(tool.settings.keys())
                imagegen = imagegen or tool.imagegen
 
        color = await ctx.embed_color()

        code = lambda s: f"`{s}`"
        list_code = lambda lst: ", ".join([code(s) for s in lst]) or "*None*"
        chance = lambda f: f"{f * 100:.2f}%"

        def channel_field(label: str, mode: str, ids: list[int]):
            embed1.add_field(
                name=f"{'Whitelisted' if mode == 'whitelist' else 'Blacklisted'} {label}",
                value=" ".join([f"<#{cid}>" for cid in ids]) or "*None*",
                inline=False,
            )
 
        embed1 = discord.Embed(title="🤖 Agent Settings", color=color)
  
        channel_field("Channels", config.channel_mode.value, config.channels.value)
        channel_field("Auto Channels", config.auto_channel_mode.value, config.auto_channels.value)
        if imagegen:
            channel_field("Generation Channels", config.generation_channel_mode.value, config.generation_channels.value)

        embed1.add_field(name="Tools", value=list_code(tools), inline=False)
        embed1.add_field(name="Tool Settings", value=f"{len(tool_settings)} keys set", inline=False)

        embed2 = discord.Embed(title="⚙️ Agent Module Settings", color=color)

        embed2.add_field(name="Recaller Model", value=code(config.model_recaller.value))
        embed2.add_field(name="Responder Model", value=code(config.model_responder.value))
        embed2.add_field(name="Memorizer Model", value=code(config.model_memorizer.value))

        embed2.add_field(name="Recaller Effort", value=code(config.effort_recaller.value))
        embed2.add_field(name="Responder Effort", value=code(config.effort_responder.value))
        embed2.add_field(name="Memorizer Effort", value=code(config.effort_memorizer.value))
 
        embed2.add_field(name="Captioner Model", value=code(config.model_captioner.value))
        embed2.add_field(name="Autoreacter Model", value=code(config.model_autoreacter.value))
        embed2.add_field(name="Default Image Model", value=code(config.image_model_default.value))
 
        embed2.add_field(name="Allow Memorizer", value=code(config.allow_memorizer.value))
        embed2.add_field(name="Memorizer Alerts", value=code(config.memorizer_alerts.value))
        embed2.add_field(name="Memorizer User Only", value=code(config.memorizer_user_only.value))

        embed2.add_field(name="Autoreacter Cooldown", value=code(f"{config.autoreacter_cooldown_minutes.value} minutes"))
        embed2.add_field(name="Autoreacter Chance", value=code(chance(config.autoreacter_chance.value)))
        embed2.add_field(name="Autoreacter Chance (Images)", value=code(chance(config.autoreacter_chance_images.value)))
 
        embed2.add_field(name="Autoresponder Cooldown", value=code(f"{config.autoresponder_cooldown_minutes.value} minutes")) 
        embed2.add_field(name="Autoresponder Chance", value=code(chance(config.autoresponder_chance.value)))

        embed3 = discord.Embed(title="📊 Agent Limits", color=color)
        for name, field in (
            ("Response Tokens", config.response_tokens),
            ("Max Images", config.max_images),
            ("Max Tool Depth", config.max_tool_depth),
            ("Backread Tokens", config.backread_tokens),
            ("Backread Messages", config.backread_messages),
            ("Backread Short", config.backread_short),
            ("Max Tool Length", config.max_tool),
            ("Max Quote Length", config.max_quote),
            ("Max Text File Length", config.max_text_file),
            ("Max Image Resolution", config.max_image_resolution),
            ("Max Caption Resolution", config.max_caption_resolution),
        ):
            embed3.add_field(name=name, value=code(field.value))

        embed4 = discord.Embed(title="🌐 Agent Global Settings", color=color)

        embed4.add_field(name="Startup Status", value=code(self.config.status.value), inline=False)

        embed4.add_field(name="Response Timeout", value=code(f"{self.config.response_timeout.value} seconds"))
        embed4.add_field(name="Slow Response Timer", value=code(f"{self.config.slow_timer.value} seconds"))
        embed4.add_field(name="Extended Logging", value=code(self.config.extended_logging.value))

        embed4.add_field(name="Slow Emoji", value=self.config.slow_emoji.value)
        embed4.add_field(name="No Response Emoji", value=self.config.noresponse_emoji.value)
        embed4.add_field(name="Blocked Emoji", value=self.config.blocked_emoji.value)
 
        await ctx.send(embeds=[embed1, embed2, embed3, embed4])


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
            lines = [f"`{key}`: `{setting_values.get(key, default) or '(empty)'}`" for key, default in setting_dict.items()]
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

    @agentconfig.command(name="image_default")  # type: ignore[untyped-decorator]
    async def agentconfig_image_default(self, ctx: commands.Context, *, selection: str = "") -> None:
        """Set the guild's default image family or an exact available checkpoint."""
        assert ctx.guild
        field = self.config[ctx.guild].image_model_default
        selection = selection.strip()
        if not selection:
            await ctx.reply(f"Default image model: `{field.value}`", mention_author=False)
            return
        if selection.lower() == "reset":
            selection = "moss"
        import aiohttp
        from arcenciel.agent_models import FAMILIES, choose_model
        from arcenciel.arcenciel import Arcenciel
        from arcenciel.utils import ImageGenError

        arcenciel = self.bot.get_cog("Arcenciel")
        if not isinstance(arcenciel, Arcenciel) or arcenciel.api is None:
            await ctx.reply("The Arc en Ciel generator is unavailable.", mention_author=False)
            return
        try:
            options, catalog = await arcenciel.api.refresh_moss_profile()
            choice = choose_model(options, catalog, default=selection)
        except (ValueError, ImageGenError, aiohttp.ClientError, TimeoutError) as error:
            await ctx.reply(f"Invalid image default: {error}", mention_author=False)
            return
        stored = selection.lower() if selection.lower() in FAMILIES else choice.name
        await field.set(stored)
        await ctx.reply(f"Default image model: `{stored}`", mention_author=False)



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
            return await ctx.reply(f"Current model for the {module} is {fields[module].value}")

        if "$" in model:
            await ctx.reply("This model will be used through OpenWebui, and things may break unexpectedly.")
        elif "/" in model:
            try:
                is_valid = await is_valid_openrouter_model(self.session, model)
            except Exception as error:
                log.error(f"Failed to contact OpenRouter. {type(error).__name__}: {error}")
                await ctx.reply("There was an error trying to contact OpenRouter, but the model will be changed anyway.")
            if not is_valid:
                return await ctx.reply("That is not a valid OpenRouter model! You may browse models [here](<https://openrouter.ai/discover>).")
        elif model.strip().lower() not in VISION_MODELS:
            return await ctx.reply("Invalid model!\nValid models are " + ",".join([f"`{m}`" for m in VISION_MODELS]))
        
        await fields[module].set(model.strip().lower())
        await ctx.tick(message="Model changed.")

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
