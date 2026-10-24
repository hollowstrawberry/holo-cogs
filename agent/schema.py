from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

import discord
from pydantic import BaseModel

StructuredObject = dict[str, Any]
AgentContent = dict[str, (str | dict[str, str])]
AgentMessage = dict[str, (str | list[AgentContent])]
CacheMode = Literal["implicit", "fixed_prefix"]


@dataclass(frozen=True)
class ParsedMessageResult:
    message_id: int
    gpt_message: AgentMessage
    tokens: int
    num_images: int


@dataclass(frozen=True)
class ImageSource:
    message_id: int
    attachment: discord.Attachment | None = None
    att_index: int = 0
    url: str | None = None


@dataclass(frozen=True)
class DiscordMessageImageCandidates:
    message: discord.Message
    priority: list[ImageSource]
    caption: list[ImageSource]

    
@dataclass(frozen=True)
class DiscordMessageResolvedImages:
    message_id: int
    image_contents: list[AgentContent]
    attachment_captions: dict[int, str]
    url_captions: dict[str, str]
    generated_image: dict[str, str] | None


@dataclass
class TokensDetailsResult:
    system: int = 0
    schema: int = 0
    memories: int = 0
    backread: int = 0
    cached: int = 0
    thinking: int = 0
    tools: int = 0
    captioner: tuple[int, int] | int = 0
    recaller: tuple[int, int] | int = 0
    memorizer: tuple[int, int] | int = 0


@dataclass
class CompletionResult:
    elapsed_ms: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | str = "unknown"
    messages: int = 0
    images: int = 0
    tool_calls: int = 0
    tokens: TokensDetailsResult = field(default_factory=TokensDetailsResult)

    def add_cost(self, cost: float):
        if isinstance(self.cost, str):
            self.cost = cost
        else:
            self.cost += cost

@dataclass
class ReactionResult:
    emote: str | None = None
    elapsed_ms: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | str = "unknown"


@dataclass(frozen=True)
class MemoryChangeResult:
    name: str
    before: str | None
    after: str | None


# Structured Outputs

class MemoryChange(BaseModel):
    action_type: Literal["create", "append", "modify", "delete"]
    memory_name: str
    memory_content: str

class MemoryChangeList(BaseModel):
    memory_changes: list[MemoryChange]

class MessageReaction(BaseModel):
    reason: str
    emote: str


# Function calling

@dataclass(frozen=True)
class Parameters:
    properties: dict
    required: list = field(default_factory=list)
    type: str = field(default="object", init=False)

@dataclass(frozen=True)
class Function:
    name: str
    description: str
    parameters: Parameters

@dataclass(frozen=True)
class ToolCall:
    function: Function
    type: str = field(default="function", init=False)
