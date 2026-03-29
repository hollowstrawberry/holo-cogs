from enum import Enum
from typing import Coroutine
from datetime import datetime
from dataclasses import dataclass, field

import discord
from discord.ext import commands


class SplitType(Enum):
    HORIZONTAL = "split-horizontal-2"
    VERTICAL = "split-vertical-2"


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
    split_type: SplitType
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
    image: ImageToImageParams | None = None
    regions: ImageRegionalParams | None = None
