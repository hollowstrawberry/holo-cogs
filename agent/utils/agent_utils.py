import aiohttp
import xmltodict
from io import BytesIO
from copy import deepcopy
from base64 import b64encode
from openai import Omit

from agent.types import AgentContent, AgentMessage, AgentValue, ContextMessage, ImageQuality, ImageSource, CacheMode
from agent.constants import CACHE_CONTROL, IMAGE_TOKEN_COUNT


def is_bot_command(message: AgentMessage, prefixes: list[str]):
    content = message["content"]
    if not isinstance(content, str):
        content = next((text for cnt in content if (text := cnt.get("text"))), "")
    return any(f"<content>{prefix}" in content for prefix in prefixes)

def prepare_messages(messages: list[AgentMessage], mode: CacheMode) -> list[AgentMessage]:
    """Copies the message list and applies cache breakpoints if applicable."""
    messages = deepcopy(messages)
    if mode == "implicit":
        return messages
    clear_cache_breakpoints(messages)
    breakpoints: list[AgentMessage] = []
    if mode in ("multiple", "cumulative"):
        breakpoints.append(messages[0])  # system prompt
    last = next((msg for msg in reversed(messages) if msg["role"] == "user"), messages[-1])
    if last not in breakpoints:
        breakpoints.append(last)
    for message in breakpoints:
        contents: str | list[AgentContent] = message["content"]
        if isinstance(contents, str):
            contents = message["content"] = [{"type": "text", "text": contents}]
        for cnt in reversed(contents):
            if cnt.get("type") == "text" and cnt.get("text"):
                cnt["cache_control"] = CACHE_CONTROL
                break
    return messages

def clear_cache_breakpoints(messages: list[AgentMessage]) -> None:
    for msg in messages:
        msg.pop("cache_control", None)
        if "content" in msg and isinstance(msg["content"], list):
            for cnt in msg["content"]:
                cnt.pop("cache_control", None)

def make_text_content(content: str, cache=False) -> AgentContent:
    return {
        "type": "text",
        "text": content,
        **({"cache_control": CACHE_CONTROL} if cache else {}),
    }

def make_image_content(b: bytes | BytesIO, low_detail: bool = False) -> AgentContent:
    b = b.read() if isinstance(b, BytesIO) else b
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:image/png;base64,{b64encode(b).decode()}",
            **({"detail": "low"} if low_detail else {}),     
        }
    }

def make_caption_content(src: ImageSource, caption: str) -> AgentContent:
    caption_obj = {
        "image_caption": {
            "#text": caption,
            **({"@type": "video_frame"} if src.is_video else {}),
            **({"@filename": src.attachment.filename} if src.attachment else {"@url": src.url}),
        }
    }
    return make_text_content(xmltodict.unparse(caption_obj, full_document=False))

def make_video_placeholder_content(url: str):
    obj = {
        "video_frame": {
            "@url": url,
            "@audio": "false"
        }
    }
    return make_text_content(xmltodict.unparse(obj, full_document=False))

def build_image_contents(msg: ContextMessage, max_quality: ImageQuality, use_captions: bool) -> list[AgentContent]:
    contents: list[AgentContent] = []
    for slot in msg.slots:
        quality = min(max_quality, slot.quality)
        if not slot.entry or quality == ImageQuality.NONE:
            continue
        if slot.src.is_video:
            contents.append(make_video_placeholder_content(slot.src.url))
        if quality == ImageQuality.FULL and slot.entry.image_full:
            contents.append(make_image_content(slot.entry.image_full))
        elif use_captions and slot.entry.caption:
            contents.append(make_caption_content(slot.src, slot.entry.caption))
        elif not use_captions and slot.entry.image_low:
            contents.append(make_image_content(slot.entry.image_low, low_detail=True))
    return contents

def text_content_only(messages: list[AgentMessage]) -> list[AgentMessage]:
    """Strips any non-text contents from the chat history."""
    temp_messages = deepcopy(messages)
    for msg in temp_messages:
        if not isinstance(msg["content"], str):
            msg["content"] = [cnt for cnt in msg["content"] if cnt.get("type") == "text"]
    return temp_messages

def adjusted_effort(model: str, effort: str) -> str | Omit:
    """Transforms an effort value into a value that is valid for the given model."""
    if "gpt-4" in model:
        return Omit()
    # very non-exhaustive list of edge cases to prevent BadRequests
    if effort in ("none", "minimal"):
        if "codex" in model or "gemini-3." in model:
            return "low"
        if model in ("gpt-5", "gpt-5-mini", "gpt-5-nano"):
            return "minimal"
        return "none"
    return effort

def image_tokens(model: str) -> tuple[int, int]:
    for pattern, low, high in IMAGE_TOKEN_COUNT:
        if pattern.match(model):
            return low, high
    return IMAGE_TOKEN_COUNT[-1][1:]

def clean_model(model: str) -> str:
    return model.replace("$", "")  # identifies openwebui model

async def is_valid_openrouter_model(session: aiohttp.ClientSession, model: str) -> bool:
    if model.count("/") != 1:
        return False
    author, slug = model.split("/")
    async with session.get(f"https://openrouter.ai/api/v1/models/{author}/{slug}/endpoints") as resp:
        if resp.status == 404:
            return False
        resp.raise_for_status()
    return True
