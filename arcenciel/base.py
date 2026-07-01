import discord
from typing import Coroutine
from datetime import datetime
from collections import defaultdict
from expiringdict import ExpiringDict
from redbot.core import Config, commands
from redbot.core.bot import Red

from arcenciel.comfy import ComfyMetadata
from arcenciel.schema import ImageGenParams, QueuedImageGen
from arcenciel.constants import DISCORD_EPOCH_DATETIME


class ArcencielBase(commands.Cog):

    def __init__(self, bot: Red):
        self.bot: Red = bot
        self.autocomplete_cache: dict[str, dict[str, str]] = defaultdict(dict)
        self.queued_images: dict[str, QueuedImageGen] = {}
        from arcenciel.arcenciel_api import ArcEnCielAPI
        self.api: ArcEnCielAPI | None = None
        self.resource_cache: dict[str, str] = {}
        self.resource_not_found_cache: dict[str, bool] = ExpiringDict(max_len=100, max_age_seconds=24*60*60)

        self.config = Config.get_conf(None, identifier=75567113, cog_name="AImage")
        self.config.register_global(**{
            "resource_cache": {},
            "nsfw": True,
            "loading_emoji": "⏳",
            "arcenciel_emoji": "🌐",
            "blacklist_regex": "",
            "negative_prompt": "worst quality, low quality",
            "cfg": 5,
            "sampling_steps": 24,
            "sampler": "euler_ancestral",
            "checkpoint": None,
            "vae": None,
            "adetailer": False,
            "width": 1024,
            "height": 1024,
            "max_img2img": 2048,
            "scheduler": "normal",
        })
        self.config.register_guild(**{
            "enabled": False,
        })
        self.config.register_user(**{
            "quota_start": DISCORD_EPOCH_DATETIME.isoformat(),
            "quota_progress": 0,
            "checkpoint": "",
        })
        self.config.register_role(**{
            "quota": 0,
        })

    async def cache_set(self, hint: str, hyperlink: str | None) -> None:
        if hyperlink is None:
            self.resource_not_found_cache[hint] = True
        else:
            self.resource_cache[hint] = hyperlink
            async with self.config.resource_cache() as cache:
                cache[hint] = hyperlink

    async def generate_image(self,
                             context: commands.Context | discord.Interaction,
                             payload: dict | None = None,
                             params: ImageGenParams | None = None,
                             callback: Coroutine | None = None,
                             message_content: str | None = None
                             ) -> None:
        raise NotImplementedError
    
    async def resolve_arcenciel_resources(self, metadata: ComfyMetadata) -> list[str]:
        raise NotImplementedError

    async def update_autocomplete_cache(self):
        raise NotImplementedError
