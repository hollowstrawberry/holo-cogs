from dataclasses import dataclass, field
from typing import Coroutine, Optional, Union

import discord
from discord.ext import commands


@dataclass
class QueuedImageGen:
    id: str
    payload: dict
    user: discord.Member
    channel: discord.abc.MessageableChannel
    context: Union[commands.Context, discord.Interaction]
    callback: Optional[Coroutine]
    message_content: Optional[str]

@dataclass
class ImageResponse:
    data: Optional[bytes] = None
    payload: dict = field(default_factory=dict)
    is_nsfw: bool = False
    info_string: str = ""
    extension: str = "png"

@dataclass
class ImageGenParams:
    prompt: str
    negative_prompt: Optional[str] = None
    style: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    cfg: Optional[float] = None
    sampler: Optional[str] = None
    scheduler: Optional[str] = None
    steps: Optional[int] = None
    seed: int = -1
    variation: int = 0
    variation_seed: int = -1
    checkpoint: Optional[str] = None
    vae: Optional[str] = None
    lora: str = ""
    subseed: int = -1
    subseed_strength: float = 0.0
    # img2img
    init_image: bytes = field(default_factory=bytes)
    denoising: Optional[float] = None
