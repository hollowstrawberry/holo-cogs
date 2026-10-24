"""Checks and typed boundaries for optional third-party utility cogs."""

from copy import copy
from typing import Protocol, runtime_checkable

import discord
from redbot.core import Config, commands


@runtime_checkable
class BirthdayBackend(Protocol):
    config: Config

    async def check_if_setup(self, guild: discord.Guild) -> bool: ...


@runtime_checkable
class ReminderBackend(Protocol):
    MAX_REMINDER_LENGTH: int

    async def insert_reminder(self, user_id: int, reminder: dict[str, object]) -> bool: ...


async def command_allowed(ctx: commands.Context, command: commands.Command, *, visible: bool = False) -> bool:
    """Use a separate context: Red's permission checks can modify its state."""
    check_ctx = copy(ctx)
    check_ctx.command = command
    if any(not item.is_enabled(ctx.guild) for item in (command, *command.parents)):
        return False
    if ctx.author.bot or not await ctx.bot.allowed_by_whitelist_blacklist(ctx.author):
        return False
    if not await ctx.bot.ignored_channel_or_guild(check_ctx):
        return False
    if ctx.guild and command.cog and await ctx.bot.cog_disabled_in_guild(command.cog, ctx.guild):
        return False
    try:
        if not await ctx.bot.can_run(check_ctx, call_once=True):
            return False
        if visible:
            return bool(await command.can_see(check_ctx))
        return bool(await command.can_run(check_ctx, check_all_parents=True))
    except commands.CommandError:
        return False


async def utility_cog(ctx: commands.Context, cog_name: str, command_name: str) -> commands.Cog | None:
    """Bind writes to the member who directly addressed the bot."""
    if (
        not ctx.guild
        or not isinstance(ctx.author, discord.Member)
        or ctx.bot.user is None
        or ctx.bot.user not in ctx.message.mentions
    ):
        return None
    cog = ctx.bot.get_cog(cog_name)
    command = ctx.bot.get_command(command_name)
    if cog is None or command is None or command.cog is not cog:
        return None
    if not await command_allowed(ctx, command):
        return None
    return cog
