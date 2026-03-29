from typing import Literal
from pydantic import BaseModel
from dataclasses import dataclass, field


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
    type: str = "object"

@dataclass(frozen=True)
class Function:
    name: str
    description: str
    parameters: Parameters

@dataclass(frozen=True)
class ToolCall:
    function: Function
    type: str = "function"

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
    image: None = None
    regions: None = None