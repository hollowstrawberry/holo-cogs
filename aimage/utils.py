import logging
import asyncio
import discord
from redbot.core import commands

from aimage.constants import LORA_REGEX, VIEW_TIMEOUT, UUID_PREFIX_REGEX, NUMERIC_PREFIX_REGEX, LORA_PREFIX_REGEX

log = logging.getLogger("red.bz_cogs.aimage")


class ImageGenError(ValueError):
    pass


async def send_response(context: commands.Context | discord.Interaction, **kwargs) -> discord.Message:
    if isinstance(context, discord.Interaction):
        if context.response.is_done():
            if "file" in kwargs:
                kwargs["attachments"] = [kwargs["file"]]
                del kwargs["file"]
            return await context.edit_original_response(**kwargs)
        else:
            return await context.followup.send(**kwargs)
    else:
        msg = await context.send(**kwargs)
        asyncio.create_task(context.message.remove_reaction("⏳", context.bot.user))
        return msg
    
def is_nsfw(channel: discord.abc.Messageable):
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
    name = LORA_PREFIX_REGEX.sub("", name)
    return name

async def delete_button_after(msg: discord.Message):
    await asyncio.sleep(VIEW_TIMEOUT)
    try:
        await msg.edit(view=None)
    except Exception:
        return

def parse_loras(payload: dict):
    for lora in LORA_REGEX.findall(payload["prompt"]):
        tag, name, weight = lora
        name = f"{name.replace('.safetensors', '')}.safetensors"
        payload.setdefault("loras", [])
        if any(lora["name"] == name for lora in payload["loras"]):
            continue
        payload["loras"].append({
            "name": name,
            "weight": weight,
        })
        payload["prompt"] = payload["prompt"].replace(tag, "")
