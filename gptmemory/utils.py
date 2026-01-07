import re
import discord
import discord.ext.commands as commands
import trafilatura
from io import BytesIO
from re import Match
from copy import deepcopy
from base64 import b64encode
from typing import Optional, List
from PIL import Image, UnidentifiedImageError

from gptmemory.constants import MAX_MESSAGE_LENGTH, CODEBLOCK_PATTERN


def sanitize(text: str) -> str:
    special_characters = "[]"
    for c in special_characters:
        text = text.replace(c, "")
    return text

def farenheit_to_celsius(match: Match) -> str:
    f = float(match.group(1))
    c = (f - 32) * 5.0/9.0
    return f"{round(c)}°C/{round(f)}°F"

def make_image_content(fp: BytesIO) -> dict:
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:image/png;base64,{b64encode(fp.read()).decode()}"
        }
    }

def process_image(buffer: BytesIO, size: int) -> Optional[BytesIO]:
    try:
        image = Image.open(buffer)
    except UnidentifiedImageError:
        return None
    width, height = image.size
    image_resolution = width * height
    target_resolution = size*size
    if image_resolution > target_resolution:
        scale_factor = (target_resolution / image_resolution) ** 0.5
        image = image.resize((int(width * scale_factor), int(height * scale_factor)), Image.Resampling.LANCZOS)
    fp = BytesIO()
    image.save(fp, "PNG")
    fp.seek(0)
    return fp

def get_text_contents(messages: List[dict]):
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

async def chunk_and_send(ctx: commands.Context, full_text: str):
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
        if m := CODEBLOCK_PATTERN.match(line):
            if m.group(1):
                in_code = True
                code_lang = m.group(1)
            else:
                in_code = not in_code
        if len(current) + len(line) > MAX_MESSAGE_LENGTH:
            flush_chunk()
        current += line

    flush_chunk()

    first_reply = True
    for chunk in chunks:
        if first_reply:
            await ctx.reply(chunk, allowed_mentions=discord.AllowedMentions.none(), mention_author=False)
            first_reply = False
        else:
            await ctx.send(chunk, allowed_mentions=discord.AllowedMentions.none())


def adjusted_effort(model: str, effort: str) -> str:
   if effort == "minimal" and "/" not in model and model not in ("gpt-5", "gpt-5-mini" "gpt-5-nano"):
       return "none"
   else:
       return effort
   

def format_arcenciel_model(data: dict) -> str:
    description = trafilatura.extract(data['description']) or data['description'] or "(Empty)"
    versions = sorted(data.get("versions", []), key=lambda v: v['id'], reverse=True)
    model_info = f"[[ Model name: {data['title']} ]] [Model URL: https://arcenciel.io/models/{data['id']}] [Type: {data['type']}] [Uploader: {data['uploader']['username']}] [Versions: {len(versions)}]"
    versions_info = ""
    for i, version in enumerate(versions):
        versions_info += f"\n[[ [Version name: {version['versionName']}] [Base model: {version['baseModel']}] [Published: {version['publishedAt']}]"
        if i == 0 and data['type'] == "LORA":
            versions_info += " [Activation tags:]"
            filename = version.get('originalName') or version.get('fileName')
            filename = re.sub(r"[^a-zA-Z0-9. _-]", "_", filename).replace(".safetensors", "") # sanitize
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
        versions_info += " ]]"
    content = f"{model_info} [Model description:] {description}\n{versions_info}"
    return content
