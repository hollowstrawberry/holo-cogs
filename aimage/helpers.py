import asyncio
from typing import Union

import aiohttp
import discord
from redbot.core import commands

from aimage.constants import VIEW_TIMEOUT, UUID_PREFIX_REGEX, NUMERIC_PREFIX_REGEX


async def send_response(context: Union[commands.Context, discord.Interaction], **kwargs) -> discord.Message:
    if isinstance(context, discord.Interaction):
        return await context.followup.send(**kwargs)
    else:
        msg = await context.send(**kwargs)
        asyncio.create_task(context.message.remove_reaction("⏳", context.bot.user))
        return msg
    
def is_nsfw(channel: discord.abc.MessageableChannel):
    if isinstance(channel, discord.TextChannel):
        return channel.nsfw
    elif isinstance(channel, discord.Thread) and channel.parent:
        return channel.parent.nsfw
    else:
        return False


def round_to_nearest(x, base):
    return int(base * round(x/base))

def clean_tag(tag: str) -> str:
    if len(tag) > 3:
        return tag.replace("_", " ").replace("(", "\\(").replace(")", "\\)")
    else:
        return tag
    
def clean_model(name: str) -> str:
    name = UUID_PREFIX_REGEX.sub("", name)
    name = NUMERIC_PREFIX_REGEX.sub("", name)
    return name


# https://github.com/hollowstrawberry/crab-cogs/blob/b1f28057ae9760dbc1d51dadb290bdeb141642bf/novelai/novelai.py#L200C1-L200C74
async def delete_button_after(msg: discord.Message):
    await asyncio.sleep(VIEW_TIMEOUT)
    try:
        await msg.edit(view=None)
    except Exception:
        return
