import io
import discord
from typing import Optional
from difflib import get_close_matches
from redbot.core import commands

from agent.base import AgentCogBase
from agent.config import AgentCogGuildConfig
from agent.config_base import ConfigField
from agent.schema import MemoryChangeResult
from agent.constants import DISCORD_EPOCH_DATETIME
from agent.views.memory_info import MemoryInfoView
from agent.views.memory_list import MemoryListView
from agent.views.memory_change import MemoryChangeView
from agent.views.prompt_show import PromptView
from agent.views.prompt_panel import PromptPanelView
from agent.views.prompt_store import PromptRef, PromptStore


class AgentCogCommands(AgentCogBase):

    @staticmethod
    def prompt_fields(config: AgentCogGuildConfig) -> dict[str, ConfigField[str]]:
        return {
            "recaller":      config.prompt_recaller,
            "responder":     config.prompt_responder,
            "memorizer":     config.prompt_memorizer,
            "autoresponder": config.prompt_autoresponder,
            "captioner":     config.prompt_captioner,
            "autoreacter":   config.prompt_autoreacter,
        }

    @commands.command(name="prompt")
    async def prompt_cmd(self, ctx: commands.Context, module: Optional[str]) -> None:
        """
        View or edit LLM prompts.
        The recaller grabs relevant memories.
        The responder sends the chat message.
        The autoresponder sends random chat messages.
        The memorizer edits memories.
        """
        assert ctx.guild
        config = self.config[ctx.guild]
        if module and module.lower().strip() in ("full", "whole", "file", "all"):
            prompt = config.prompt_responder.value
            prompt_keys = config.prompt_keys.value
            for key, value in prompt_keys.items():
                prompt = prompt.replace(f"{{{key}}}", value)
            with io.StringIO() as fp:
                fp.write(prompt)
                fp.seek(0)
                file = discord.File(fp, f"prompt_{ctx.message.id}.txt")  # type: ignore
            await ctx.send(file=file)
            return
        view = await self.prompt_cmd_show(ctx, module) if module else await self.prompt_cmd_edit(ctx)
        if view:
            view.message = await ctx.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        if ctx.bot_permissions.manage_messages:
            await ctx.message.delete()

    async def prompt_cmd_show(self, ctx: commands.Context, module: str) -> PromptView | None:
        config = self.config[ctx.guild]
        store = PromptStore(self.prompt_fields(config), config.prompt_keys)
        ref = PromptRef("module" if module in store.modules else "key", module)
        if store.get(ref) is None:
            await ctx.send("Prompt not found. Use the prompt panel to select an available key.", delete_after=60)
            return None
        return PromptView(store, ref, self.bot.is_owner, await ctx.embed_color())

    async def prompt_cmd_edit(self, ctx: commands.Context) -> PromptPanelView:
        assert ctx.guild
        config = self.config[ctx.guild]
        return PromptPanelView(
            PromptStore(self.prompt_fields(config), config.prompt_keys), self.bot.is_owner,
            f"{ctx.guild.me.name} Prompt Panel", await ctx.embed_color(),
        )


    @commands.command(name="forget")
    async def command_forget(self, ctx: commands.Context):
        """Temporarily makes the bot only read messages past a certain point."""
        await self.config[ctx.channel].start.set(ctx.message.created_at)
        await ctx.tick(message="✅")

    @commands.has_permissions(manage_messages=True)
    @commands.command(name="unforget")
    async def command_unforget(self, ctx: commands.Context):
        """Undoes the effect of [p]forget"""
        await self.config[ctx.channel].start.set(DISCORD_EPOCH_DATETIME)
        await ctx.tick(message="✅")
        # remove the previous [p]forget, this is not perfect but it's not important anyway
        assert ctx.guild
        if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            limit = self.config[ctx.guild].backread_messages.value
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
        await self.command_memory(ctx, name=ctx.author)

    @commands.command(name="memory", aliases=["memories"], invoke_without_subcommand=True)
    @commands.guild_only()
    async def command_memory(self, ctx: commands.Context, *, name: discord.Member | str | None):
        """View all memories or a specific memory, for the bot LLM"""
        if isinstance(name, discord.Member):
            name = name.name
        assert ctx.guild
        memory = self.config[ctx.guild].memory.value
        if not name:
            if memory:
                view = MemoryListView(list(memory.keys()))
                view.message = await ctx.send(view=view)
            else:
                await ctx.send("No memories...", delete_after=60)
        elif memory:
            if name not in memory:
                matches = get_close_matches(name, memory)
                if matches:
                    name = matches[0]
            if name in memory:
                async def edit_memory(content: str):
                    if not content:
                        await self.command_deletememory(ctx, name=name)
                    else:
                        await self.command_setmemory(ctx, name=name, content=content)
                view = MemoryInfoView(name, memory[name], edit_memory, self.bot.is_owner)
                view.message = await ctx.send(view=view)
        else:
            await ctx.send(f"No memory of `{name}`", delete_after=60)
        if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            try:
                await ctx.message.delete()
            except discord.DiscordException:
                pass

    @commands.command(name="deletememory", aliases=["delmemory"]) # type: ignore
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def command_deletememory(self, ctx: commands.Context, *, name: str):
        """Delete an LLM memory"""
        memory = self.config[ctx.guild].memory
        if before := memory.value.pop(name, None):
            await memory.save()
            view = MemoryChangeView([MemoryChangeResult(name, before, None)], standalone=True)
            view.message = await ctx.send(view=view)
        else:
            await ctx.send(f"No memory of `{name}`", delete_after=60)
        assert ctx.guild
        if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            try:
                await ctx.message.delete()
            except discord.DiscordException:
                pass

    @commands.command(name="setmemory") # type: ignore
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def command_setmemory(self, ctx: commands.Context, name: str, *, content: str):
        """Overwrite an LLM memory"""
        name = name.replace("`", "")
        if not name:
            return await ctx.send("Invalid name")
        if len(name) > 1000:
            return await ctx.send("Name too long")
        memory = self.config[ctx.guild].memory
        before = memory.value.get(name)
        memory.value[name] = content
        await memory.save()
        view = MemoryChangeView([MemoryChangeResult(name, before, content)], standalone=True)
        view.message = await ctx.send(view=view)
        assert ctx.guild
        if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            try:
                await ctx.message.delete()
            except discord.DiscordException:
                pass
