import re
import asyncio
import contextlib
import discord
from copy import copy
from typing import Callable, Type, TypeVar
from redbot.core import commands
from redbot.core.bot import Red

from .image_utils import make_circle
from agent.types import HasId
from agent.constants import MAX_MESSAGE_LENGTH, MENTION_PATTERN

T = TypeVar("T")


def fix_emote(bot: Red) -> Callable[[re.Match], str]:
    def repl(match: re.Match) -> str:
        _, name, eid = match.groups()
        emote = None
        if eid:
            emote = bot.get_emoji(int(eid))
        if not emote:
            emote = discord.utils.get(bot.emojis, name=name)
        if emote:
            return str(emote)
        if "<" in match.group(0) and ">" in match.group(0):
            return ""
        return match.group(0)
    return repl

def is_nsfw(channel: discord.abc.Messageable) -> bool:
    if isinstance(channel, discord.TextChannel):
        return channel.nsfw
    elif isinstance(channel, discord.Thread) and channel.parent:
        return channel.parent.nsfw
    else:
        return False

def button_label(button: discord.Button) -> str:
    emoji_name = button.emoji if not button.emoji or isinstance(button.emoji, str) else f":{button.emoji.name}:"
    return " ".join([s for s in (emoji_name, button.label) if s])

def get_cog(bot: Red, cog_type: Type[T]) -> T | None:
    cog = bot.get_cog(cog_type.__name__)
    if isinstance(cog, cog_type):
        return cog
    return None

def parse_mentions(guild: discord.Guild, text: str) -> str:
    def replace(match: re.Match) -> str:
        prefix, name = match.groups()
        group = guild.members if prefix == "@" else guild.channels
        obj = discord.utils.find(lambda o: name == o.name, group)
        return obj.mention if obj else match.group(0)
    return MENTION_PATTERN.sub(replace, text)

async def command_allowed(ctx: commands.Context, command: commands.Command, *, visible: bool = False) -> bool:
    assert(isinstance(ctx.bot, Red))
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
            return await command.can_see(check_ctx)
        return await command.can_run(check_ctx, check_all_parents=True)
    except commands.CommandError:
        return False

AVATAR_EMOTE: discord.Emoji | None = None

async def get_bot_avatar_emote(bot: discord.Client) -> discord.Emoji:
    """Use the bot's avatar cropped into a circle as an application emoji"""
    global AVATAR_EMOTE
    NAME = "bot_avatar"
    if not AVATAR_EMOTE:
        emojis = await bot.fetch_application_emojis()
        AVATAR_EMOTE = discord.utils.find(lambda a: a.name == NAME, emojis)
    if not AVATAR_EMOTE:
        assert bot.user
        data = await bot.user.display_avatar.replace(size=128, format="png").read()
        result = await asyncio.to_thread(make_circle, data)
        AVATAR_EMOTE = await bot.create_application_emoji(name=NAME, image=result)
    return AVATAR_EMOTE

async def chunk_and_send(ctx: commands.Context,
                         full_text: str,
                         embed: discord.Embed = None,
                         view: discord.ui.View = None,
                         files: list[discord.File] = None,
                         do_reply: bool = False
                        ):
    base_lines = full_text.splitlines(keepends=True)
    lines = []
    for base_line in base_lines:
        if len(base_line) > MAX_MESSAGE_LENGTH:
            while len(base_line) > MAX_MESSAGE_LENGTH:
                lines.append(base_line)
                base_line = base_line[:MAX_MESSAGE_LENGTH]
        else:
            lines.append(base_line)

    chunks = []
    current = ""
    in_code = False
    code_lang = ""

    def flush_chunk():
        nonlocal current, in_code, code_lang
        if in_code:
            current += "```\n"  # close open fence
        if current:
            chunks.append(current)
        # start new
        current = ""
        if in_code:
            # re-open fence with language
            current += f"```{code_lang}\n"

    for line in lines:
        if m := re.match(r"^```(\w*)\s*$", line):
            if m.group(1):
                in_code = True
                code_lang = m.group(1)
            else:
                in_code = not in_code
        if len(current) + len(line) > MAX_MESSAGE_LENGTH:
            flush_chunk()
        current += line

    flush_chunk()

    for i, chunk in enumerate(chunks):
        current_reference, current_embed, current_view = None, None, None
        current_files = []
        if do_reply and i == 0:
            current_reference = ctx.message
        if i == len(chunks) - 1:
            current_embed, current_view = embed, view
            current_files = files or []
        msg = await ctx.send(
            chunk,
            embed=current_embed,
            view=current_view,
            files=current_files,
            reference=current_reference,
            allowed_mentions=discord.AllowedMentions.none(),
            mention_author=False
        )
        if view and hasattr(view, "message"):
            setattr(view, "message", msg)


@contextlib.asynccontextmanager
async def bot_is_typing(channel: discord.abc.Messageable):
    """Like channel.typing but doesn't stop execution if typing fails."""
    assert isinstance(channel, HasId)

    async def keep_typing():
        while True:
            await asyncio.wait_for(channel._state.http.send_typing(channel.id), timeout=2.0)
            await asyncio.sleep(5.0)

    task = asyncio.create_task(keep_typing())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
