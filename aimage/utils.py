import io
import logging
import discord
from PIL import Image, ImageDraw
from rapidfuzz import fuzz
from redbot.core import commands

from aimage.schema import SplitType
from aimage.constants import LORA_PATTERN, UUID_PREFIX_PATTERN, NUMERIC_PREFIX_PATTERN, LORA_PREFIX_PATTERN, LORA_PATTERN

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
    
def is_nsfw(channel: discord.abc.Messageable) -> bool:
    if isinstance(channel, discord.TextChannel):
        return channel.nsfw
    elif isinstance(channel, discord.Thread) and channel.parent:
        return channel.parent.nsfw
    else:
        return False

def round_to_nearest(x, base) -> int:
    return int(base * round(x/base))

def scale_to_size(width: int, height: int, pixels: int) -> tuple[int, int]:
    scale = (pixels / (width * height)) ** 0.5
    return int(width * scale), int(height * scale)

def normalize_image(b: bytes, max_pixels: int) -> bytes:
    image = Image.open(io.BytesIO(b))
    if image.width*image.height > max_pixels:
        width, height = scale_to_size(image.width, image.height, max_pixels)
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    fp = io.BytesIO()
    image.save(fp, "PNG")
    return fp.read()

def filter_names(options: dict, current: str, strict: bool = False) -> dict:
    results = {}
    ratios = [(item, fuzz.partial_ratio(current.lower(), item.lower())) for item in options.keys()]
    sorted_options = sorted(ratios, key=lambda x: x[1], reverse=True)
    for item, ratio in sorted_options:
        if strict and ratio < 75:
            continue
        results[item] = options[item]
    return results

def clean_tag(tag: str) -> str:
    if len(tag) > 3:
        return tag.replace("_", " ").replace("(", "\\(").replace(")", "\\)")
    else:
        return tag
    
def clean_model(name: str) -> str:
    name = UUID_PREFIX_PATTERN.sub("", name)
    name = NUMERIC_PREFIX_PATTERN.sub("", name)
    name = LORA_PREFIX_PATTERN.sub("", name)
    return name

def parse_loras(payload: dict) -> None:
    for lora in LORA_PATTERN.findall(payload["prompt"]):
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
        for region in payload.get("attentionCouple", {}).get("regions", []):
            region["prompt"] = LORA_PATTERN.sub("", region["prompt"]).strip()

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

def build_split_masks(width: int, height: int, split_percent: float, layout: str) -> list[tuple[str, bytes]]:
    if layout == SplitType.VERTICAL.value:
        split_y = clamp(round(height * (split_percent / 100.0)), 1, height - 1)
        rects = [
            (0, 0, width, split_y),               # region 1
            (0, split_y, width, height - split_y) # region 2
        ]
    else:
        split_x = clamp(round(width * (split_percent / 100.0)), 1, width - 1)
        rects = [
            (0, 0, split_x, height),              # region 1
            (split_x, 0, width - split_x, height) # region 2
        ]

    out: list[tuple[str, bytes]] = []
    for i, rect in enumerate(rects, start=1):
        filename = f"attention-region-{i}-{width}x{height}.png"
        out.append((filename, make_region_mask(width, height, rect)))
    return out

def edit_regional_prompts(shared_prompt: str, *prompts: str) -> list[str]:
    shared_prompt = shared_prompt.strip(" ,") + ", "
    edited_prompts = list(prompts)
    for i, prompt in enumerate(prompts):
        prompt = shared_prompt + prompt.replace("||", "").replace("[R1]", "").replace("[R2]", "").strip()
        prompt = LORA_PATTERN.sub("", prompt).strip()
        if "masterpiece" not in prompt and "best quality" not in prompt:
            prompt = "masterpiece, best quality, " + prompt
        edited_prompts[i] = prompt
    final_prompt = " || ".join(edited_prompts)
    return [final_prompt, *edited_prompts]
