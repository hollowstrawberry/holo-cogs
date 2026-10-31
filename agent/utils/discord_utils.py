import re
import asyncio
import discord
import contextlib
from copy import copy
from typing import Type, TypeVar
from redbot.core import commands
from redbot.core.bot import Red

from .image_utils import crop_circle
from .component_utils import resolve_components
from agent.types import MessageableChannel, HasMessage
from agent import constants

T = TypeVar("T")

def get_cog(bot: Red, cog_type: Type[T]) -> T | None:
    """Grab the cog with the given type's name and type, if present."""
    cog = bot.get_cog(cog_type.__name__)
    if isinstance(cog, cog_type):
        return cog
    return None

def is_nsfw(channel: discord.abc.Messageable) -> bool:
    if isinstance(channel, discord.TextChannel):
        return channel.nsfw
    elif isinstance(channel, discord.Thread) and channel.parent:
        return channel.parent.nsfw
    else:
        return False

def button_label(button: discord.Button) -> str:
    """The button's emoji and label if either or both are present."""
    emoji_name = button.emoji if not button.emoji or isinstance(button.emoji, str) else f":{button.emoji.name}:"
    return " ".join([s for s in (emoji_name, button.label) if s])

async def backread_with_quote(ctx: commands.Context, limit: int) -> list[discord.Message]:
    """Return the trigger message's quoted message, then the trigger message, then the rest of the chat history."""
    quoted = None
    ref = ctx.message.reference
    if ref and ref.message_id and ref.channel_id == ctx.channel.id:
        try:
            quoted = ref.cached_message or await ctx.channel.fetch_message(ref.message_id)
        except discord.DiscordException:
            pass
    messages = [
        *([quoted] if quoted else []),
        ctx.message,
        *[message async for message in ctx.channel.history(limit=limit, before=ctx.message)]
    ]
    return list(await asyncio.gather(*(resolve_components(message) for message in messages)))

def parse_mentions(guild: discord.Guild, text: str) -> str:
    """Convert @name mentions into <@id> mentions (users/roles/channels)."""
    def repl(match: re.Match) -> str:
        prefix, name = match.groups()
        group = [*guild.members, *guild.roles] if prefix == "@" else guild.channels
        obj = discord.utils.find(lambda o: name == o.name, group)
        return obj.mention if obj else match.group(0)
    return constants.RESOLVED_MENTION_PATTERN.sub(repl, text)

def resolve_mentions(bot: Red, guild: discord.Guild | None, text: str) -> str:
    """Convert <@id> mentions into @name mentions (users/roles/channels)."""
    def repl(match: re.Match) -> str:
        prefix, id = match.groups()
        if prefix == "@&":
            role = guild.get_role(int(id)) if guild else None
            return f"@{role.name}" if role else "@unknown"
        elif prefix == "#":
            channel = bot.get_channel(int(id))
            return f"#{channel.name}" if isinstance(channel, (discord.abc.GuildChannel, discord.Thread)) else "#unknown"
        else:
            user = bot.get_user(int(id))
            return f"@{user.name}" if user else "@unknown"
    return constants.MENTION_PATTERN.sub(repl, text)

def parse_emotes(bot: Red, text: str) -> str:
    """Convert any :name: emotes with no id and any <:name:id> emotes with the wrong name into <:name:id> emotes."""
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
    return constants.INCOMPLETE_EMOTE_PATTERN.sub(repl, text)

def resolve_emotes(bot: Red, text: str) -> str:
    """Convert <:name:id> emotes into :name: emotes, gets the emote's true name from the id if possible."""
    def repl(match: re.Match) -> str:
        _, name, eid = match.groups()
        emote = bot.get_emoji(int(eid))
        return f":{emote.name if emote else name}:"
    return constants.EMOTE_PATTERN.sub(repl, text)

async def command_allowed(ctx: commands.Context, command: commands.Command, *, visible_only: bool = False) -> bool:
    """Checks if the current user can use/see a specific command."""
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
        if visible_only:
            return await command.can_see(check_ctx)
        return await command.can_run(check_ctx, check_all_parents=True)
    except commands.CommandError:
        return False

AVATAR_EMOTE: discord.Emoji | None = None

async def get_bot_avatar_emote(bot: discord.Client) -> discord.Emoji:
    """Use the bot's avatar cropped into a circle as an application emoji."""
    global AVATAR_EMOTE
    NAME = "bot_avatar"
    if not AVATAR_EMOTE:
        emojis = await bot.fetch_application_emojis()
        AVATAR_EMOTE = discord.utils.find(lambda a: a.name == NAME, emojis)
    if not AVATAR_EMOTE:
        assert bot.user
        data = await bot.user.display_avatar.replace(size=128, format="png").read()
        result = await asyncio.to_thread(crop_circle, data)
        AVATAR_EMOTE = await bot.create_application_emoji(name=NAME, image=result)
    return AVATAR_EMOTE


async def chunk_and_send(
    ctx: commands.Context,
    full_text: str,
    embed: discord.Embed = None,
    view: discord.ui.View = None,
    files: list[discord.File] = None,
    do_reply: bool = False,
) -> None:
    """
    Splits a message that is too long into multiple messages.
    The first message will do a reply, the last message will contain embeds and views and files
    """
    max_length = constants.MAX_MESSAGE_LENGTH
    base_lines = full_text.splitlines(keepends=True)
    lines = []
    for base_line in base_lines:
        if len(base_line) > max_length:
            while len(base_line) > max_length:
                lines.append(base_line)
                base_line = base_line[:max_length]
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
        if len(current) + len(line) > max_length:
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
            mention_author=False,
        )
        if isinstance(view, HasMessage):
            view.message = msg


@contextlib.asynccontextmanager
async def bot_is_typing(channel: MessageableChannel):
    """Like channel.typing but doesn't stop execution if typing fails."""
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
