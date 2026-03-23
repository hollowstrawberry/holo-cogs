import json
import logging
import asyncio
import discord
from typing import Optional, Union
from collections import OrderedDict
from redbot.core import commands
from sd_prompt_reader.image_data_reader import ImageDataReader

from aimage.constants import LORA_REGEX, PARAMS_BLACKLIST, VIEW_TIMEOUT, UUID_PREFIX_REGEX, NUMERIC_PREFIX_REGEX, LORA_PREFIX_REGEX

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
    name = LORA_PREFIX_REGEX.sub("", name)
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
    
    for key, value in metadata.parameter.items():
        if len(output_dict) > 24 or any(blacklisted in key for blacklisted in PARAMS_BLACKLIST):
            continue
        output_dict[key.title()] = value

    if "Comfy" in metadata._tool:
        try:
            workflow = json.loads("{" + metadata.raw.split("{", 1)[1])
            for node_id, node in workflow.items():
                if node["class_type"] == "LoraLoader":
                    lora_name = node.get("inputs", {}).get("lora_name", "").replace(".safetensors", "")
                    lora_weight = node.get("inputs", {}).get("strength_model", 1.0)
                    if lora_name:
                        output_dict["Prompt"] += f" <lora:{lora_name}:{lora_weight}>"  # type: ignore                    
                elif node_id == "extra_seed_extra_noise":
                    output_dict["Extra Seed"] = node.get("inputs", {}).get("noise_seed", -1)
                elif node_id == "extra_seed_noised_latent_blend":
                    output_dict["Extra Seed Strength"] = round(1.0 - node.get("inputs", {}).get("blend_factor", 1.0), 4)
        except Exception:
            log.warning("Loading comfy metadata", exc_info=True)
            
    return output_dict

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
