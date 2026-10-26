import discord
from typing import Any, Literal, Protocol, runtime_checkable
from pydantic import BaseModel
from dataclasses import dataclass, field

StructuredObject = dict[str, Any]
AgentValue = dict[str, str | dict[str, str]]
AgentContent = dict[str, (str | AgentValue)]
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
    low_priority: list[ImageSource]


@dataclass(frozen=True)
class ImageProcessingResult:
    src: ImageSource
    data: bytes | None
    caption: str | None
    low_detail: bool = False

    
@dataclass(frozen=True)
class DiscordMessageResolvedImages:
    message_id: int
    image_contents: list[AgentContent]
    attachment_captions: dict[int, str | None]
    url_captions: dict[str, str | None]
    generated_image: dict[str, str] | None
    low_detail_count: int


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
    cost: float | str

    def add_cost(self, cost: object) -> None:
        val = cost.cost if isinstance(cost, HasCost) else cost
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not -float("inf") < val < float("inf"):
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
    cost: float | str = "unknown"
    messages: int = 0
    images: int = 0
    tool_calls: int = 0
    tokens: TokensDetailsResult = field(default_factory=TokensDetailsResult)

@dataclass
class ReactionResult(CostResult):
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


# Protocols

@runtime_checkable
class ImageScanner(Protocol): # crab-cogs
    image_cache: dict[int, tuple[dict[int, object], dict[int, bytes]]]

    async def grab_metadata_dict(self, message: discord.Message) -> dict: ...

@runtime_checkable
class HasError(Protocol):
    error: object

@runtime_checkable
class HasCost(Protocol):
    cost: object

@runtime_checkable
class HasLength(Protocol):
    length: float

@runtime_checkable
class HasId(Protocol):
    id: int
