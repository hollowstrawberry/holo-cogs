import math
import asyncio
import discord
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from pydantic import BaseModel
from dataclasses import dataclass, field

StructuredObject = dict[str, Any]
"""Signifies a dict that will be unparsed into XML."""

AgentValue = dict[str, str | dict[str, str]]
AgentContent = dict[str, (str | AgentValue)]
AgentMessage = dict[str, (str | list[AgentContent])]

CacheMode = Literal["implicit", "fixed_prefix"]

if TYPE_CHECKING:
    MessageableChannel = discord.abc.MessageableChannel
else:  # I hate future imports
    MessageableChannel = discord.abc.Messageable


class ImageQuality(IntEnum):
    NONE = 0
    LOW = 1
    FULL = 2


@dataclass(frozen=True)
class ImageSource:
    url: str
    message_id: int
    attachment: discord.Attachment | None = None
    att_index: int = 0
    proxy_url: str | None = None
    is_video: bool = False

    @property
    def key(self) -> int | str:
        return self.attachment.id if self.attachment else self.url


@dataclass
class ImageEntry:
    image_full: bytes | None = None
    image_low: bytes | None = None
    caption: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)


@dataclass
class ImageSlot:
    src: ImageSource
    quality: ImageQuality
    entry: ImageEntry | None = None


@dataclass(frozen=True)
class ContextMessage:
    message: discord.Message
    text: str
    text_tokens: int
    slots: list[ImageSlot] = field(default_factory=list)  # includes the images of the message it quotes


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
class CostResult:
    cost: float | str = "unknown"

    def add_cost(self, cost: object) -> None:
        val = cost.cost if isinstance(cost, HasCost) else cost
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val):
            return
        if isinstance(self.cost, str):
            self.cost = val
        else:
            self.cost += val


@dataclass
class CompletionResult(CostResult):
    elapsed_ms: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    messages: int = 0
    images: int = 0  # full quality
    images_low: int = 0
    tool_calls: int = 0
    tokens: TokensDetailsResult = field(default_factory=TokensDetailsResult)


@dataclass
class ReactionResult(CostResult):
    emote: str | None = None
    elapsed_ms: float = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class MemoryChangeResult:
    name: str
    before: str | None
    after: str | None


# LLM Structured Outputs

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


# Protocols

@runtime_checkable
class LinkFixer(Protocol):
    """Cog from crab-cogs."""
    async def get_fixed_message(self, message: discord.Message) -> discord.Message | None: ...

@runtime_checkable
class ImageScanner(Protocol):
    """Cog from crab-cogs."""
    image_cache: dict[int, tuple[dict[int, object], dict[int, bytes]]]

    async def grab_metadata_dict(self, message: discord.Message) -> dict: ...

@runtime_checkable
class HasError(Protocol):
    error: object

@runtime_checkable
class HasCost(Protocol):
    cost: float

@runtime_checkable
class HasLength(Protocol):
    length: Any

@runtime_checkable
class HasMessage(Protocol):
    message: discord.Message | None
