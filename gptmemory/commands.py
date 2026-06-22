import io
import discord
from typing import Optional
from difflib import get_close_matches
from redbot.core import commands

from gptmemory.base import GptMemoryBase, GptMemoryGuildConfig
from gptmemory.schema import MemoryChangeResult
from gptmemory.config import ConfigField
from gptmemory.constants import DISCORD_EPOCH_DATETIME
from gptmemory.views.memory_info import MemoryInfoView
from gptmemory.views.memory_list import MemoryListView
from gptmemory.views.memory_change import MemoryChangeView
from gptmemory.views.prompt_show import PromptView
from gptmemory.views.prompts_edit import PromptsEditView


class GptMemoryCommands(GptMemoryBase):

    @staticmethod
    def prompt_fields(config: GptMemoryGuildConfig) -> dict[str, ConfigField[str]]:
        return {
            "recaller":      config.prompt_recaller,
            "responder":     config.prompt_responder,
            "memorizer":     config.prompt_memorizer,
            "autoresponder": config.prompt_autoresponder,
            "captioner":     config.prompt_captioner,
            "autoreacter":   config.prompt_autoreacter,
        }

    @commands.command(name="prompt")
    async def prompt_cmd(self, ctx: commands.Context, module: Optional[str]):
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
            embed = discord.Embed(color=await ctx.embed_color())
            embed.set_author(name=f"{ctx.guild.me.name} Prompt Panel", icon_url=ctx.guild.me.display_avatar.url)
            view.message = await ctx.send(embed=embed, view=view)
        if ctx.bot_permissions.manage_messages:
            await ctx.message.delete()

    async def prompt_cmd_show(self, ctx: commands.Context, module: str) -> PromptView | None:
        config = self.config[ctx.guild]
        prompt = ""
        if field := self.prompt_fields(config).get(module):
            edit = field.set
            prompt = field.value
        else:
            if module not in config.prompt_keys.value:
                await ctx.send(f"Prompt `{module}` not found.", delete_after=60)
                return None
            async def edit_callback(pr: str):
                config.prompt_keys.value[module] = pr
                await config.prompt_keys.save()
            edit = edit_callback
            prompt = config.prompt_keys.value[module]
        return PromptView(module, prompt, edit, self.bot.is_owner)

    async def prompt_cmd_edit(self, ctx: commands.Context) -> PromptsEditView:
        assert ctx.guild
        config = self.config[ctx.guild]
        async def edit_callback(name: str, prompt: str):
            assert ctx.guild
            if field := self.prompt_fields(config).get(name):
                await field.set(prompt)
            else:
                config.prompt_keys.value[name] = prompt
                await config.prompt_keys.save()
        prompts = {
            **config.prompt_keys.value,
            **{name: field.value for name, field in self.prompt_fields(config).items()}
        }
        return PromptsEditView(prompts, edit_callback, self.bot.is_owner)


    @commands.command(name="forget")
    async def command_forget(self, ctx: commands.Context):
        """Temporarily makes the bot only read messages past a certain point."""
        await self.config.channel[ctx.channel.id].start.set(ctx.message.created_at)
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
                view = MemoryInfoView(name, memory[name])
                view.message = await ctx.send(view=view)
        else:
            await ctx.send(f"No memory of `{name}`", delete_after=60)
        if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            await ctx.message.delete()

    @commands.command(name="deletememory", aliases=["delmemory"]) # type: ignore
    @commands.has_permissions(manage_guild=True)
    @commands.guild_only()
    async def command_deletememory(self, ctx: commands.Context, *, name: str):
        """Delete an LLM memory"""
        if (memory := self.config[ctx.guild].memory.value) and name in memory:
            before = memory[name]
            del memory[name]
            await self.config[ctx.guild].memory.save()
            view = MemoryChangeView([MemoryChangeResult(name, before, None)], standalone=True)
            view.message = await ctx.send(view=view)
        else:
            await ctx.send(f"No memory of `{name}`", delete_after=60)
        assert ctx.guild
        if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            await ctx.message.delete()

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
        memory = self.config[ctx.guild].memory.value
        before = memory.get(name)
        memory[name] = content
        await self.config[ctx.guild].memory.save()
        view = MemoryChangeView([MemoryChangeResult(name, before, content)], standalone=True)
        view.message = await ctx.send(view=view)
        assert ctx.guild
        if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            await ctx.message.delete()
