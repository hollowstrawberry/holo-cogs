import logging
import discord
from typing import Iterable
from redbot.core import commands
from redbot.core.bot import Red, Config
from redbot.core.core_commands import Core
from redbot.cogs.downloader.downloader import Downloader

from .utils import get_result_set, bot_is_typing

log = logging.getLogger("red.holo-cogs.fastupdate")


class FastUpdate(commands.Cog):
    """
    Adds an update command that is more efficient and prettier than the default, although less stable.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, 12367)
        self.config.register_global(**{
            "failed_cogs": [],
            "allowed_repos": []
        })

    async def save_failed_cogs(self, *, add: Iterable[str] | None = None, remove: Iterable[str] | None = None) -> None:
        if not add and not remove:
            return
        failed_cogs = set(await self.config.failed_cogs())
        failed_cogs.update(set(add or []))
        failed_cogs.difference_update(set(remove or []))
        failed_cogs_lst = list(failed_cogs)
        await self.config.failed_cogs.set(failed_cogs_lst)

    @commands.is_owner()
    @commands.bot_has_permissions(embed_links=True)
    @commands.command(name="update")
    async def update_command(self, ctx: commands.Context):
        """
        Update cogs without reinstalling dependencies.
        Updated cogs are reloaded automatically.
        Cogs that failed to update or load are retried next time.
        """
        try:
            async with bot_is_typing(ctx.channel):
                await self.update_logic(ctx)
        except Exception:
            log.exception("Unexpected error when running the update command.")
            await ctx.send("Unexpected error, please check the logs")

    async def update_logic(self, ctx: commands.Context):
        assert self.bot.user
        downloader = self.bot.get_cog(Downloader.__name__)
        core = self.bot.get_cog(Core.__name__)
        if not isinstance(downloader, Downloader) or not isinstance(core, Core):
            return await ctx.send("Downloader cog not found.")

        allowed_repo_names = await self.config.allowed_repos()
        allowed_repos = [repo for repo in downloader._repo_manager.repos if repo.name in allowed_repo_names]
        cogs_to_check, _ = await downloader._get_cogs_to_check(repos=allowed_repos or None)
        cogs_to_check = {cog for cog in cogs_to_check if not cog.pinned}

        # update
        failed_updates: list[str] = []
        updated_cogs: set[str] = set()
        if cogs_to_check:
            cogs_to_update, _ = await downloader._available_updates(cogs_to_check)
            if cogs_to_update:
                updated_cogs, _ = await downloader._update_cogs_and_libs(ctx, cogs_to_update, [], cogs_to_check)
                failed_updates = sorted(cog.name for cog in cogs_to_update if cog.name not in updated_cogs)

        previously_failed: set[str] = set(await self.config.failed_cogs())
        to_reload = list(dict.fromkeys([*sorted(updated_cogs), *sorted(previously_failed)]))  # specific order
        if not to_reload and not failed_updates:
            if allowed_repos:
                return await ctx.send("Everything is up to date on the relevant repos.")
            else:
                return await ctx.send("Everything is up to date.")

        # unload
        unload_result = await core._unload(to_reload)
        was_loaded = get_result_set(unload_result, "unloaded_packages")
        to_load = [cog for cog in to_reload if cog in was_loaded or cog in previously_failed]  # keep unloaded cogs unloaded
        await self.save_failed_cogs(add=to_load)  # failed until proven loaded

        # load
        load_result = await core._load(to_load) if to_load else {}
        now_loaded = get_result_set(load_result, "loaded_packages")
        missing = get_result_set(load_result, "notfound_packages", "invalid_pkg_names")
        failed_to_load = get_result_set(load_result, "failed_packages", "failed_with_reason_packages")
        await self.save_failed_cogs(add=failed_to_load, remove = now_loaded | missing)

        # pretty embed
        def state_of(cog: str) -> str:
            if cog in failed_updates:
                return "❌ Failed to update" + (" again" if cog in previously_failed else "")
            if cog in updated_cogs:
                if cog in failed_to_load:
                    return "❌ Updated but failed to load"
                if cog in now_loaded:
                    return "✅ Updated and back online" if cog in previously_failed else "✅ Updated"
                return "✅ Updated (not loaded)"
            # previously failed
            return "✅ Back online" if cog in now_loaded else "⚠️ Still offline"

        entries = list(dict.fromkeys(failed_updates + to_reload))
        embed = discord.Embed(color=await ctx.embed_color())
        embed.set_author(icon_url=self.bot.user.display_avatar.url, name=f"{self.bot.user.name} Updates")
        for cog in entries[:25]:
            if cog not in missing:
                embed.add_field(name=cog, value=state_of(cog))
        if len(entries) > 25:
            embed.set_footer(text=f"+{len(entries) - 25} more not shown")
        await ctx.send(embed=embed)


    @commands.group(name="fastupdateset", aliases=["setfastupdate"])
    async def fastupdate_set(self, ctx: commands.Context):
        """Configure the fastupdate cog"""

    @fastupdate_set.group(name="repo", aliases=["repos"])
    async def fastupdate_repos(self, ctx: commands.Context):
        """Sets allowed repos to limit the [p]update command"""

    @fastupdate_repos.command("list", aliases=["show", "view"])
    async def fastupdate_repos_list(self, ctx: commands.Context):
        """Show all the allowed repos"""
        allowed_repos: list[str] = await self.config.allowed_repos()
        await ctx.send(f"Allowed repos for update command: " + (f"`{' '.join(allowed_repos)}`" if allowed_repos else "*(None)*"))

    @fastupdate_repos.command("add")
    async def fastupdate_repos_add(self, ctx: commands.Context, *repos: str):
        """Add allowed repos"""
        allowed_repos: set[str] = set(await self.config.allowed_repos())
        allowed_repos |= set(repos)
        await self.config.allowed_repos.set(list(allowed_repos))
        await ctx.tick(message="✅")

    @fastupdate_repos.command("remove")
    async def fastupdate_repos_remove(self, ctx: commands.Context, *repos: str):
        """Remove allowed repos"""
        allowed_repos: set[str] = set(await self.config.allowed_repos())
        allowed_repos -= set(repos)
        await self.config.allowed_repos.set(list(allowed_repos))
        await ctx.tick(message="✅")
