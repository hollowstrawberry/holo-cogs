import logging
import asyncio
import discord
from io import BytesIO
from redbot.core import commands

from gptmemory.utils import get_filename, clean_tag, normalize_image
from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase

log = logging.getLogger("gptmemory.imagetagger")


class ImageTaggingFunctionCall(FunctionCallBase):
    settings = {"tagging_emoji": "🖼️"}
    schema = ToolCall(
        Function(
            name="image_tagging",
            description="Infer booru tags to describe a user-provided image.",
            parameters=Parameters(
                properties={
                    "filename": {
                        "type": "string",
                        "description": "Which image file to infer tags for.",
                    },
                },
                required=["filename"],
            )))

    async def find_image(self, filename: str) -> discord.Attachment | str | None:
        assert self.ctx.guild
        limit = await self.cog.config.guild(self.ctx.guild).backread_messages()
        messages = [message async for message in self.ctx.channel.history(limit=limit)]
        if self.ctx.message and self.ctx.message.reference and self.ctx.message.reference.message_id:
            quoted = await self.ctx.channel.fetch_message(self.ctx.message.reference.message_id)
            messages.insert(0, quoted)
        for message in messages:
            for attachment in message.attachments:
                if attachment.filename == filename:
                    return attachment
            for embed in message.embeds:
                if embed.image and embed.image.url and filename == get_filename(embed.image.url):
                    return embed.image.url
        return None
    
    async def run(self, arguments: dict) -> str:
        assert self.ctx.guild
        filename: str = arguments.get("filename", "")
        if not filename:
            return "[Error: No filename provided]"
        aimage: commands.Cog | None = self.ctx.bot.get_cog("AImage")
        if not aimage:
            return "[Error: `aimage` cog not installed, please notify the bot owner]"
        image_source = await self.find_image(filename.strip())
        if not image_source:
            return f"[Error: Can't find image '{filename}' in recent chat logs]"

        emoji = await self.get_setting("tagging_emoji")
        asyncio.create_task(self.ctx.message.add_reaction(emoji))
        try:
            if isinstance(image_source, discord.Attachment):
                image_bytes = await image_source.read()
            else:
                async with self.cog.session.get(image_source) as response:
                    response.raise_for_status()
                    image_bytes = await response.read()
            max_resolution = await self.cog.config.guild(self.ctx.guild).max_image_resolution()
            fp = await asyncio.to_thread(normalize_image, image_bytes, max_resolution**2)
            if not fp:
                return f"[The image appears to be corrupted or invalid]"
            tags = await aimage.api.interrogate(fp, filename.rsplit(".", 1)[0] + ".png")  # type: ignore
            return f"`{', '.join([clean_tag(tag) for tag in tags])}`"
        except Exception as error:
            log.exception("LLM autotag")
            return f"[Failed to tag image] [[[ {type(error).__name__}: {error} ]]]"
