import discord
import discord.ext.commands as commands
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
