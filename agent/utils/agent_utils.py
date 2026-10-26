import json
import hashlib
import aiohttp
from io import BytesIO
from copy import deepcopy
from base64 import b64encode
from pydantic import BaseModel
from openai import Omit

from agent.schema import AgentContent, AgentMessage, AgentValue, CacheMode
from agent.constants import CACHE_CONTROL, IMAGE_TOKEN_COUNT


def is_bot_command(message: AgentMessage, prefixes: list[str]):
    return message["role"] == "user" and any(f"<content>{prefix}" in message["content"] for prefix in prefixes)

def prepare_messages(messages: list[AgentMessage], mode: CacheMode) -> list[AgentMessage]:
    """Copy the initial context; a fixed boundary never advances with tool results."""
    if mode not in ("implicit", "fixed_prefix"):
        raise ValueError("Unknown cache mode")
    prepared = deepcopy(messages)
    for message in prepared:
        message.pop("cache_control", None)
        content = message["content"]
        if isinstance(content, list):
            for part in content:
                part.pop("cache_control", None)
    if mode == "fixed_prefix":
        candidates = [message for message in prepared if message["role"] == "user"]
        if not candidates:
            candidates = prepared[:1]
        for message in reversed(candidates):
            content: str | list[AgentContent] = message["content"]
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
                message["content"] = content
            for part in reversed(content):
                if part.get("type") == "text" and part.get("text"):
                    part["cache_control"] = CACHE_CONTROL
                    return prepared
    return prepared

def clear_cache_breakpoints(messages: list[AgentMessage]) -> None:
    for msg in messages:
        if not isinstance(msg, dict) or not isinstance(msg["content"], list):
            continue
        for cnt in msg["content"]:
            if isinstance(cnt, dict):
                cnt.pop("cache_control", None)

def fingerprint(value: object) -> str:
    """Hash structured requests without logging prompt or image contents."""
    def json_default(value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json", exclude_unset=True)
        raise TypeError(f"Cannot fingerprint {type(value).__name__}")
    encoded = json.dumps(value, default=json_default, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()

def make_text_content(content: str, cache=False) -> AgentContent:
    obj: AgentContent = {
        "type": "text",
        "text": content,
    }
    if cache:
        obj["cache_control"] = CACHE_CONTROL
    return obj

def make_image_content(b: bytes | BytesIO, low_detail: bool = False) -> AgentContent:
    b = b.read() if isinstance(b, BytesIO) else b
    image_url: AgentValue = {"url": f"data:image/png;base64,{b64encode(b).decode()}"}
    if low_detail:
        image_url["detail"] = "low"
    return {
        "type": "image_url",
        "image_url": image_url
    }

def get_text_contents(messages: list[AgentMessage]) -> list[AgentMessage]:
    """
    Converts a list of mixed OpenAI message dicts into a list of text-only message dicts,
    and overrides all the message roles to user.
    """
    temp_messages: list[AgentMessage] = []
    for msg in deepcopy(messages):
        if isinstance(msg["content"], str):
            temp_messages.append({
                "role": "user",
                "content": msg["content"]
            })
        else:
            text_parts: list[str] = []
            for cnt in msg["content"]:
                text = cnt.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            if text_parts:
                temp_messages.append({
                    "role": "user",
                    "content": "\n".join(text_parts)
                })
    return temp_messages

def adjusted_effort(model: str, effort: str) -> str | Omit:
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
