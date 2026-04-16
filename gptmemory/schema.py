from typing import Literal
from discord import Enum
from pydantic import BaseModel
from dataclasses import dataclass, field


GptImageContent = list[dict[str, str]]
GptMessage = dict[str, (str | GptImageContent)]


class SplitType(Enum):
    HORIZONTAL = "split-horizontal-2"
    VERTICAL = "split-vertical-2"


# Results

@dataclass
class TokensDetailsResult:
    system: int = 0
    schema: int = 0
    memories: int = 0
    backread: int = 0
    cached: int = 0
    thinking: int = 0
    tools: int = 0
    recaller: tuple[int, int] | int = 0
    memorizer: tuple[int, int] | int = 0


@dataclass
class GptMemoryResult:
    messages: int = 0
    images: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | str = "unknown"
    tokens: TokensDetailsResult = field(default_factory=TokensDetailsResult)


@dataclass
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


# Image generation

@dataclass
class ImageRegionalParams:
    prompt1: str
    prompt2: str
    split_type: str
    split_percent: int

@dataclass
class ImageGenParams:
    prompt: str
    negative_prompt: str | None = None
    style: str | None           = None
    width: int | None           = None
    height: int | None          = None
    cfg: float | None           = None
    sampler: str | None         = None
    scheduler: str | None       = None
    steps: int | None           = None
    seed: int                   = -1
    variation: int              = 0
    variation_seed: int         = -1
    checkpoint: str | None      = None
    vae: str | None             = None
    loras: list[str]            = field(default_factory=list)
    subseed: int                = -1
    subseed_strength: float     = 0.0
    image: None                 = None
    regions: ImageRegionalParams | None = None
