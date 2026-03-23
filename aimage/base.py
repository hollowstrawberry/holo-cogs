import discord
from collections import defaultdict
from typing import Coroutine, Optional, Union, Dict

from redbot.core import Config, commands
from redbot.core.bot import Red

from aimage.schema import ImageGenParams, QueuedImageGen


class AImageBase(commands.Cog):

    def __init__(self, bot: Red):
        self.bot: Red = bot
        self.config = Config.get_conf(self, identifier=75567113)
        self.autocomplete_cache: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.queued_images: Dict[str, QueuedImageGen] = {}

    async def generate_image(self,
                             context: Union[commands.Context, discord.Interaction],
                             payload: dict = None,
                             params: ImageGenParams = None,
                             callback: Optional[Coroutine] = None,
                             message_content: Optional[str] = None
                             ) -> None:
        raise NotImplementedError

    async def update_autocomplete_cache(self, guild: discord.Guild):
        raise NotImplementedError
