import logging
import asyncio
import discord
from typing import Any
from redbot.core import commands

from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase

log = logging.getLogger("gptmemory.imagetagger")


class ImageTaggingFunctionCall(FunctionCallBase):
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
    
    @staticmethod
    def clean_tag(tag: str) -> str:
        if len(tag) > 3:
            return tag.replace("_", " ").replace("(", "\\(").replace(")", "\\)")
        else:
            return tag

    async def find_attachment(self, filename: str) -> discord.Attachment | None:
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
        return None
    
    async def run(self, arguments: dict) -> str:
        filename: str = arguments.get("filename", "")
        if not filename:
            return "[Error: No filename provided]"
        log.info(filename)
        aimage: commands.Cog | None = self.ctx.bot.get_cog("AImage")
        if not aimage:
            return "[Error: `aimage` cog not installed, please notify the bot owner]"
        attachment = await self.find_attachment(filename.strip())
        if not attachment:
            return f"[Error: Can't find image '{filename}' in recent chat logs]"
        try:
            log.info("attachment")
            image_bytes = await attachment.read()
            log.info("image_bytes")
            tags = await aimage.api.interrogate(image_bytes, attachment.filename)
            log.info("interrogate")
            return f"`{', '.join([self.clean_tag(tag) for tag in tags])}`"
        except Exception as error:
            log.exception("LLM autotag")
            return f"[Failed to tag image] [[[ {type(error).__name__}: {error} ]]]"
