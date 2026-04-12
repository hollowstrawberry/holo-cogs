import os
import re
import discord
import trafilatura
from io import BytesIO
from copy import deepcopy
from base64 import b64encode
from urllib.parse import urlparse
from PIL import Image, UnidentifiedImageError
from redbot.core import commands

from gptmemory.constants import MAX_MESSAGE_LENGTH


def sanitize(text: str) -> str:
    special_characters = "[]"
    for c in special_characters:
        text = text.replace(c, "")
    return text

def clean_tag(tag: str) -> str:
    tag = tag.lower().strip()
    if len(tag) > 3:
        return tag.replace("_", " ").replace("(", "\\(").replace(")", "\\)")
    else:
        return tag

def farenheit_to_celsius(match: re.Match) -> str:
    f = float(match.group(1))
    c = (f - 32) * 5.0/9.0
    return f"{round(c)}°C/{round(f)}°F"

def make_image_content(b: bytes | BytesIO) -> dict:
    b = b.read() if isinstance(b, BytesIO) else b
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:image/png;base64,{b64encode(b).decode()}"
        }
    }

def get_filename(url: str) -> str:
    return os.path.basename(urlparse(url).path)

def find_nearest_resolution(current: tuple[int, int], targets: list[tuple[int, int]]) -> tuple[int, int]:
    ratio = current[0] / current[1]
    best_match = min(targets, key=lambda res: abs((res[0] / res[1]) - ratio))
    return best_match

def scale_to_size(width: int, height: int, pixels: int) -> tuple[int, int]:
    scale = (pixels / (width * height)) ** 0.5
    return int(width * scale), int(height * scale)

def normalize_image(b: bytes | BytesIO, max_pixels: int) -> bytes | None:
    b = b if isinstance(b, BytesIO) else BytesIO(b)
    b.seek(0)
    try:
        image = Image.open(b)
    except UnidentifiedImageError:
        return None
    if image.width*image.height > max_pixels:
        width, height = scale_to_size(image.width, image.height, max_pixels)
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    fp = BytesIO()
    image.save(fp, "PNG")
    fp.seek(0)
    return fp.read()

def get_text_contents(messages: list[dict]):
    """
    Converts a list of mixed OpenAI message dicts into a list of text-only message dicts,
    and overrides all the message roles to user.
    """
    temp_messages = []
    for msg in deepcopy(messages):
        if isinstance(msg["content"], str):
            temp_messages.append({
                "role": "user",
                "content": msg["content"]
            })
        else:
            for cnt in msg["content"]:
                if "text" in cnt:
                    temp_messages.append({
                        "role": "user",
                        "content": cnt["text"]
                    })
                break
    return temp_messages

def adjusted_effort(model: str, effort: str) -> str:
   if effort == "minimal" and "/" not in model and model not in ("gpt-5", "gpt-5-mini" "gpt-5-nano"):
       return "none"
   else:
       return effort
   
def parse_prompt(prompt: str) -> str:
    prompt = re.sub(r",?\s*\n[\n\s]*", " || ", prompt)
    return prompt

def format_arcenciel_model(data: dict) -> str:
    description = trafilatura.extract(data['description']) or data['description'] or "(Empty)"
    versions = sorted(data.get("versions", []), key=lambda v: v['id'], reverse=True)
    model_info = f"[[ Model name: {data['title']} ]] [Model URL: https://arcenciel.io/models/{data['id']}] [Type: {data['type']}] [Uploader: {data['uploader']['username']}] [Versions: {len(versions)}]"
    versions_info = ""
    for i, version in enumerate(versions):
        versions_info += f"\n[[ [Version name: {version['versionName']}] [Base model: {version['baseModel']}] [Published: {version['publishedAt']}]"
        if i <= 1 and data['type'] == "LORA":
            versions_info += " [Activation tags:]"
            filename = version['filePath'].split("/")[-1].replace(".safetensors", "")
            lora = f"<lora:{filename}:1>" if filename else ""
            if version.get('activationTags', []):
                for tags in version['activationTags']:
                    if tags.count('|') == 1:
                        tags = tags.split('|')[1].strip()
                    if tags in description:
                        description = description.replace(tags, "[tags]")
                    versions_info += f" [{lora} {tags}]"
            else:
                versions_info += f" {lora}"
        else:
            versions_info += " [Incomplete data]"
        versions_info += " ]]"
    content = f"{model_info} [Model description:] {description}\n{versions_info}"
    return content


async def chunk_and_send(ctx: commands.Context, full_text: str, do_reply: bool):
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

    for chunk in chunks:
        if do_reply:
            await ctx.reply(chunk, allowed_mentions=discord.AllowedMentions.none(), mention_author=False)
            do_reply = False
        else:
            await ctx.send(chunk, allowed_mentions=discord.AllowedMentions.none())
