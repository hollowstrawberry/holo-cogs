import discord
from typing import Coroutine
from datetime import datetime
from collections import defaultdict
from redbot.core import Config, commands
from redbot.core.bot import Red

from aimage.schema import ImageGenParams, QueuedImageGen


class AImageBase(commands.Cog):

    def __init__(self, bot: Red):
        self.bot: Red = bot
        self.autocomplete_cache: dict[str, dict[str, str]] = defaultdict(dict)
        self.queued_images: dict[str, QueuedImageGen] = {}
        self.gen_count: dict[int, int] = defaultdict(int)
        self.last_quota = datetime.min
        from aimage.arcenciel_api import ArcEnCielAPI
        self.api: ArcEnCielAPI | None = None

        self.config = Config.get_conf(self, identifier=75567113)
        default_global = {
            "nsfw": True,
            "quota": 5,
            "loading_emoji": "⏳",
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
        }
        default_guild = {
            "enabled": False,
            "vip_role": -1,
        }
        default_user = {
            "vip": False,
            "checkpoint": "",
        }
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_user)
        self.config.register_global(**default_global)


    async def generate_image(self,
                             context: commands.Context | discord.Interaction,
                             payload: dict | None = None,
                             params: ImageGenParams | None = None,
                             callback: Coroutine | None = None,
                             message_content: str | None = None
                             ) -> None:
        raise NotImplementedError

    async def update_autocomplete_cache(self):
        raise NotImplementedError
