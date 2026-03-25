from datetime import datetime
from dataclasses import dataclass, field
from typing import Coroutine

import discord
from discord.ext import commands


@dataclass
class QueuedImageGen:
    id: str
    payload: dict
    user: discord.Member
    channel: discord.abc.Messageable
    context: commands.Context | discord.Interaction
    callback: Coroutine | None
    message_content: str | None
    progress_message: discord.Message | None = None
    last_updated: datetime = datetime.min
    last_percent: int = -1
    last_eta: int | None = None

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
    lora: str                   = ""
    subseed: int                = -1
    subseed_strength: float     = 0.0
    # img2img
    image: bytes                = field(default_factory=bytes)
    image_filename: str | None  = None
    denoising: float | None     = None
    scale: float | None         = None
