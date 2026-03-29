import io
import logging
import discord
from typing import Literal
from PIL import Image, ImageDraw
from redbot.core import commands

from aimage.constants import LORA_REGEX, UUID_PREFIX_REGEX, NUMERIC_PREFIX_REGEX, LORA_PREFIX_REGEX

log = logging.getLogger("red.bz_cogs.aimage")


class ImageGenError(ValueError):
    pass


async def send_response(context: commands.Context | discord.Interaction, **kwargs) -> discord.Message | None:
    if isinstance(context, discord.Interaction):
        assert isinstance(context.channel, discord.abc.Messageable)
        if context.response.is_done():
            if "file" in kwargs:
                kwargs["attachments"] = [kwargs["file"]]
                del kwargs["file"]
            if "embed" not in kwargs:
                kwargs["embed"] = None
            msg = await context.edit_original_response(**kwargs)
        else:
            msg = await context.followup.send(**kwargs)
        try:
            return await context.channel.fetch_message(msg.id)  # the other objects expire
        except discord.NotFound:
            log.exception("Grabbing interaction message")
            return None
    else:
        msg = await context.send(**kwargs)
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

def clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, value))

def make_region_mask(width: int, height: int, rect: tuple[int, int, int, int]) -> bytes:
    # Same logic as Arc Web: black full image, target region transparent.
    img = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)

    x, y, w, h = rect
    x2 = x + max(1, w) - 1
    y2 = y + max(1, h) - 1
    draw.rectangle((x, y, x2, y2), fill=(0, 0, 0, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def build_split_masks(
    width: int,
    height: int,
    split_percent: float = 50.0,
    layout: Literal["horizontal", "vertical"] = "horizontal",
) -> list[tuple[str, bytes]]:
    if layout == "horizontal":
        split_x = clamp(round(width * (split_percent / 100.0)), 1, width - 1)
        rects = [
            (0, 0, split_x, height),              # region 1
            (split_x, 0, width - split_x, height) # region 2
        ]
    else:
        split_y = clamp(round(height * (split_percent / 100.0)), 1, height - 1)
        rects = [
            (0, 0, width, split_y),               # region 1
            (0, split_y, width, height - split_y) # region 2
        ]

    out: list[tuple[str, bytes]] = []
    for i, rect in enumerate(rects, start=1):
        filename = f"attention-region-{i}-{width}x{height}.png"
        out.append((filename, make_region_mask(width, height, rect)))
    return out