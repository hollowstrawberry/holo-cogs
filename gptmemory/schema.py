from typing import Literal, List, Optional
from pydantic import BaseModel
from dataclasses import dataclass, field


# Structured Outputs

class MemoryChange(BaseModel):
    action_type: Literal["create", "append", "modify", "delete"]
    memory_name: str
    memory_content: str

class MemoryChangeList(BaseModel):
    memory_changes: List[MemoryChange]


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
    negative_prompt: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    cfg: Optional[float] = None
    sampler: Optional[str] = None
    scheduler: Optional[str] = None
    steps: Optional[int] = None
    seed: int = -1
    checkpoint: Optional[str] = None
    vae: Optional[str] = None
    lora: str = ""
    subseed: int = -1
    subseed_strength: float = 0.0
    init_image: bytes = field(default_factory=bytes)
    denoising: Optional[float] = None
