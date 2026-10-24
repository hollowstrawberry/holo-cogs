"""Request-local cache policy. No model names or prompt instructions live here."""

import hashlib
import json
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel

from .schema import AgentMessage

CacheMode = Literal["implicit", "fixed_prefix"]


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
        # A trailing system message may be folded into systemInstruction by gateways.
        # Prefer the last user message so the boundary includes conversation history.
        candidates = [message for message in prepared if message["role"] == "user"]
        if not candidates:
            candidates = prepared[:1]
        for message in reversed(candidates):
            content = message["content"]
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
                message["content"] = content
            for part in reversed(content):
                if part.get("type") == "text" and part.get("text"):
                    part["cache_control"] = {"type": "ephemeral"}
                    return prepared
    return prepared


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_unset=True)
    raise TypeError(f"Cannot fingerprint {type(value).__name__}")


def fingerprint(value: object) -> str:
    """Hash structured requests without logging prompt or image contents."""
    encoded = json.dumps(value, default=_json_default, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
