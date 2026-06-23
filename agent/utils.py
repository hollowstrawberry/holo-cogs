import os
import re
import logging
import asyncio
import discord
import contextlib
import trafilatura
from io import BytesIO
from copy import deepcopy
from base64 import b64encode
from typing import Callable
from datetime import datetime
from urllib.parse import urlparse
from PIL import Image, UnidentifiedImageError
from redbot.core import commands
from redbot.core.bot import Red
from openai import Omit

from agent.schema import AgentImageContent, AgentMessage, StructuredObject
from agent.constants import MAX_MESSAGE_LENGTH, NEWLINE_SEPARATOR_PATTERN, DATETIME_FORMATTING, XML_TAG_PATTERN, UNCLOSED_XML_TAG_PATTERN, EMOTE_PATTERN

log = logging.getLogger("agent.utils")


def add_xml_group(obj: dict, group: list, group_name: str) -> None:
    single_name = group_name[:-1]
    if len(group) == 1:
        obj[single_name] = group[0]
    elif len(group) > 1:
        obj[group_name] = {single_name: group}

def escape_xml(s: str) -> str:
    return s.replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;").replace('"', "&quot;").replace("&", "&amp;")

def undo_xml(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&apos;", "'").replace("&quot;", '"').replace("&amp;", "&")

def fix_truncated_xml(text: str) -> str:
    text = UNCLOSED_XML_TAG_PATTERN.sub("", text)
    stack = []
    for match in XML_TAG_PATTERN.finditer(text):
        is_closing, tag_name, is_self_closing = match.groups()
        if is_self_closing:
            continue
        elif is_closing:
            if stack and stack[-1] == tag_name:
                stack.pop()
        else:
            stack.append(tag_name)
    for tag in reversed(stack):
        text += f"</{tag}>"
    return text

def fix_emote(bot: Red) -> Callable[[re.Match], str]:
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
    return repl

def clean_content(text: str) -> str:
    return EMOTE_PATTERN.sub(r":\2:", text)

def clean_tag(tag: str) -> str:
    tag = tag.lower().strip()
    if len(tag) > 3:
        return tag.replace("_", " ").replace("(", "\\(").replace(")", "\\)")
    else:
        return tag
    
def is_bot_command(message: AgentMessage, prefixes: list[str]):
    return message["role"] == "user" and any(f"<content>{prefix}" in message["content"] for prefix in prefixes)

def farenheit_to_celsius(match: re.Match) -> str:
    f = float(match.group(1))
    c = (f - 32) * 5.0/9.0
    return f"{round(c)}°C/{round(f)}°F"

def make_image_content(b: bytes | BytesIO, low_detail: bool = False) -> AgentImageContent:
    b = b.read() if isinstance(b, BytesIO) else b
    image_url = {"url": f"data:image/png;base64,{b64encode(b).decode()}"}
    if low_detail:
        image_url["detail"] = "low"
    return {
        "type": "image_url",
        "image_url": image_url
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

def normalize_image(b: bytes | BytesIO, max_pixels: int | None = None, thumbnail_size: int | None = None) -> bytes | None:
    b = b if isinstance(b, BytesIO) else BytesIO(b)
    b.seek(0)
    try:
        image = Image.open(b)
    except (UnidentifiedImageError, OSError):
        return None
    if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
        image_rgba = image.convert("RGBA")
        background = Image.new("RGB", image.size, (0, 0, 0))
        background.paste(image_rgba, (0, 0), image_rgba.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    format = "PNG"
    if max_pixels and image.width*image.height > max_pixels:
        width, height = scale_to_size(image.width, image.height, max_pixels)
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    if thumbnail_size:
        format = "JPEG"
        image.thumbnail((thumbnail_size, thumbnail_size), Image.Resampling.LANCZOS)
    fp = BytesIO()
    image.save(fp, format, quality=90)
    return fp.getvalue()

def button_label(button: discord.Button):
    emoji_name = button.emoji if not button.emoji or isinstance(button.emoji, str) else f":{button.emoji.name}:"
    return " ".join([s for s in (emoji_name, button.label) if s])

def get_text_contents(messages: list[AgentMessage]) -> list[AgentMessage]:
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

def adjusted_effort(model: str, effort: str) -> str | Omit:
    if "gpt-4" in model:
        return Omit()
    if effort in ("none", "minimal"):
        if "codex" in model:
            return "low"
        if model in ("gpt-5", "gpt-5-mini" "gpt-5-nano"):
            return "minimal"
        return "none"
    return effort
   
def clean_model(model: str) -> str:
    return model.replace("$", "")  # identifies openwebui model
   
def parse_prompt(prompt: str) -> str:
    prompt = NEWLINE_SEPARATOR_PATTERN.sub(" || ", prompt)
    return prompt

def parse_arcenciel_model(data: dict) -> StructuredObject:
    obj = {
        "@title": data["title"],
        "@type": data["type"],
        "url": f"https://arcenciel.io/models/{data['id']}",
        "uploader": data["uploader"]["username"],
        "description": trafilatura.extract(data["description"]) or data["description"] or "(Empty)",
    }
    versions_obj = []
    versions = sorted(data.get("versions", []), key=lambda v: v['id'], reverse=True)
    for i, version in enumerate(versions):
        ver_obj = {
            "@name": version["versionName"],
            "@base_model": version["baseModel"],
            "@published": datetime.fromisoformat(version["publishedAt"]).astimezone().strftime(DATETIME_FORMATTING)
        }
        if i <= 1 and data["type"] == "LORA":
            filename = version.get("filePath", "").split("/")[-1].replace(".safetensors", "").strip()
            if filename:
                ver_obj["lora"] = f"<lora:{filename}:1.0>"
            if version.get("aboutThisVersion"):
                ver_obj["description"] = version["aboutThisVersion"]
            if version.get("activationTags", []):
                tags_obj = []
                for tags in version["activationTags"]:
                    t_obj = {}
                    if tags.count("|") == 1:
                        name, tags = [t.strip() for t in tags.split("|")]
                        t_obj["@name"] = name
                    if len(tags) > 10 and tags in obj["description"]:
                        obj["description"] = obj["description"].replace(tags, "<tags>")
                    t_obj["#text"] = tags
                    tags_obj.append(t_obj)
                ver_obj["activation_tags"] = {"prompt": tags_obj}
        else:
            ver_obj["#text"] = "Incomplete data"
        versions_obj.append(ver_obj)
    obj["versions"] = {"version": versions_obj}
    return obj


async def chunk_and_send(ctx: commands.Context,
                         full_text: str,
                         embed: discord.Embed = None,
                         view: discord.ui.View = None,
                         files: list[discord.File] = None,
                         do_reply: bool = False
                        ):
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
            mention_author=False
        )
        if view and hasattr(view, "message"):
            setattr(view, "message", msg)


@contextlib.asynccontextmanager
async def bot_is_typing(channel: discord.abc.Messageable):
    """Like channel.typing but doesn't stop execution if typing fails."""
    async def keep_typing():
        while True:
            await asyncio.wait_for(channel.typing(), timeout=2.0)
            await asyncio.sleep(5.0)
            
    task = asyncio.create_task(keep_typing())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
