
import discord
from abc import ABC
from enum import Enum
from typing import Literal, NotRequired, TypedDict, Protocol, runtime_checkable
from datetime import datetime
from dataclasses import dataclass, field
from collections.abc import Coroutine, Mapping
from redbot.core import commands


class SplitType(Enum):
    HORIZONTAL = "split-horizontal-2"
    VERTICAL = "split-vertical-2"


class GeneratingV2ViewBase(discord.ui.LayoutView, ABC):
    def set_progress(self, status: str, progress: str | None = None, eta: str | None = None, step: str | None = None, preview_filename: str | None = None):
        raise NotImplementedError


@runtime_checkable
class ImageScanner(Protocol): # crab-cogs
    scan_channels: set[int]
    image_cache: dict[int, tuple[dict[int, object], dict[int, bytes]]]


@dataclass
class QueuedImageGen:
    id: str
    payload: dict
    user: discord.Member
    channel: discord.TextChannel | discord.Thread
    context: commands.Context | discord.Interaction
    callback: Coroutine | None
    message_content: str | None
    progress_message: discord.Message | None
    last_updated: datetime
    last_position: int = -1
    last_percent: int = 0
    last_eta: int = 1_000_000
    cancelled: bool = False
    pending_preview: tuple[bytes, str, int, int] | None = None
    preview_version: int = 0
    displayed_preview_version: int = 0
    preview_filename: str | None = None
    preview_step: int = 0
    preview_total_steps: int = 0
    preview_disabled: bool = False
    progress_v2_view: GeneratingV2ViewBase | None = None

@dataclass
class ImageToImageParams:
    data: bytes
    filename: str
    denoising: float
    scale: float

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
    agent_request: bool         = False
    image: ImageToImageParams | None = None
    regions: ImageRegionalParams | None = None
    # Raw options are validated into MossOptions before they reach the API.
    moss: Mapping[str, object] | None = None


# MOSS

BoxLabel = Literal["character", "object", "text"]


class MossModel(TypedDict):
    name: str
    modelCapabilities: list[str]
    storageState: str


class MossDefaults(TypedDict):
    steps: int
    cfg: float
    shift: float
    samplerName: str
    scheduler: str
    width: int
    height: int


class MossProfile(TypedDict):
    available: bool
    modes: list[str]
    maxBoxes: int
    coordinateSpace: Literal["normalized_1000"]
    defaults: MossDefaults


class MossBox(TypedDict):
    label: BoxLabel
    caption: str
    bbox: list[int]
    text: NotRequired[str]


class MossLayout(TypedDict):
    version: Literal[1]
    coordinateSpace: Literal["normalized_1000"]
    canvas: Literal["single"]
    items: list[MossBox]
    meta: NotRequired[list[str]]


class MossOptions(TypedDict, total=False):
    shift: float
    layout: MossLayout


class MossLora(TypedDict):
    name: str
    weight: float


class MossPayload(TypedDict):
    mode: Literal["txt2img"]
    prompt: str
    negativePrompt: str
    modelName: str
    seed: int
    batchSize: Literal[1]
    loras: list[MossLora]
    sfwMode: bool
    steps: int
    cfg: float
    samplerName: str
    scheduler: str
    width: int
    height: int
    moss: MossOptions
