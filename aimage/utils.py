import json
import logging
import asyncio
import discord
from typing import Optional, Union
from collections import OrderedDict
from redbot.core import commands
from sd_prompt_reader.image_data_reader import ImageDataReader

from aimage.constants import PARAMS_BLACKLIST, VIEW_TIMEOUT, UUID_PREFIX_REGEX, NUMERIC_PREFIX_REGEX

log = logging.getLogger("red.bz_cogs.aimage")

async def send_response(context: Union[commands.Context, discord.Interaction], **kwargs) -> discord.Message:
    if isinstance(context, discord.Interaction):
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
    return name

# https://github.com/hollowstrawberry/crab-cogs/blob/b1f28057ae9760dbc1d51dadb290bdeb141642bf/novelai/novelai.py#L200C1-L200C74
async def delete_button_after(msg: discord.Message):
    await asyncio.sleep(VIEW_TIMEOUT)
    try:
        await msg.edit(view=None)
    except Exception:
        return

def get_params_dict(metadata: ImageDataReader) -> Optional[dict]:
    output_dict = OrderedDict()
    output_dict["Prompt"] = metadata.positive or metadata.positive_sdxl
    output_dict["Negative Prompt"] = metadata.negative or metadata.negative_sdxl
    
    if "Comfy" in metadata._tool:
        try:
            workflow = json.loads(metadata._parser._workflow)  # type: ignore
            for node in workflow.values():
                if node["class_type"] == "LoraLoader":
                    lora_name = node.get("inputs", {}).get("lora_name", "").replace(".safetensors", "")
                    lora_weight = node.get("inputs", {}).get("strength_model", 1.0)
                    if lora_name:
                        output_dict["Prompt"] += f" <lora:{lora_name}:{lora_weight}>"  # type: ignore
        except Exception:
            log.warning("Loading comfy metadata", exc_info=True)

    for key, value in metadata.parameter.items():
        if len(output_dict) > 24 or any(blacklisted in key for blacklisted in PARAMS_BLACKLIST):
            continue
        output_dict[key.title()] = value
            
    return output_dict