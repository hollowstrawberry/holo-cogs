import logging
import asyncio
import discord
from redbot.core import commands

from gptmemory.utils import undo_xml
from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.tools.base import ToolBase

log = logging.getLogger("gptmemory.gptimage")


class GptImageTool(ToolBase):
    display_name="gptimage"
    schema = ToolCall(
        Function(
            name="generate",
            description="Generate or edit an image with GPT, suitable for general/generic content.",
            parameters=Parameters(
                properties={
                    "existing": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional. The filename of an image(s) in chat to edit or use as reference. " \
                                       'For example, <attachment filename="image.png"></attachment> would result in image.png',
                        "minItems": 0,
                        "maxItems": 3,
                    },
                    "prompt": {
                        "type": "string",
                        "description": "A prompt for image generation using natural language. " \
                                       "Making new images requires a detailed prompt. " \
                                       'Editing an existing image should start with "Keep the image the same, but..." and only the necessary changes.'
                    },
                    "resolution": {
                        "type": "string",
                        "description": "Optional. Aspect ratio for the image.",
                        "enum": ["original", "square", "portrait", "landscape"]
                    },
                },
                required=["prompt"],
            )))

    async def find_attachment(self, filename: str, messages: list[discord.Message]) -> discord.Attachment | None:
        assert self.ctx.guild
        for message in messages:
            for attachment in message.attachments:
                if attachment.filename == filename:
                    return attachment
        return None
    
    async def run(self, arguments: dict) -> dict | str:
        assert self.ctx.guild and isinstance(self.ctx.author, discord.Member) and isinstance(self.ctx.channel, (discord.TextChannel, discord.Thread))
        
        channel_mode = await self.cog.config.guild(self.ctx.guild).generation_channel_mode()
        channels = await self.cog.config.guild(self.ctx.guild).generation_channels()
        if channel_mode == "blacklist" and self.ctx.channel.id in channels \
                or channel_mode == "whitelist" and self.ctx.channel.id not in channels:
            if not self.ctx.channel.permissions_for(self.ctx.author).manage_messages:
                return "<error>Image generation is not allowed in this channel unless the user is a moderator</error>"

        existing: str | list[str] | None = arguments.get("existing")
        prompt: str = undo_xml(arguments.get("prompt", ""))
        aspect_ratio: str = arguments.get("resolution", "")

        if not prompt:
            return "<error>No prompt provided</error>"
        gptimage: commands.Cog | None = self.ctx.bot.get_cog("GptImage")
        if not gptimage:
            return "<error>`gptimage` cog not installed, please notify the bot owner</error>"

        aspect_ratio = aspect_ratio.lower().strip()
        if aspect_ratio == "original" and not existing:
            return "<error>You selected original resolution but didn't provide an existing image</error>"

        if aspect_ratio == "square":
            resolution = "1024x1024"
        elif aspect_ratio in ("portrait", "vertical"):
            resolution = "1024x1536"
        elif aspect_ratio in ("landscape", "horizontal"):
            resolution = "1536x1024"
        else:
            resolution = None

        attachments: list[discord.Attachment] = []
        if existing:
            existing_list = existing if isinstance(existing, list) else [existing]
            limit = await self.cog.config.guild(self.ctx.guild).backread_messages()
            messages = [message async for message in self.ctx.channel.history(limit=limit)]
            if self.ctx.message and self.ctx.message.reference and self.ctx.message.reference.message_id:
                quoted = self.ctx.message.reference.cached_message or await self.ctx.channel.fetch_message(self.ctx.message.reference.message_id)
                messages.insert(0, quoted)
            for filename in existing_list:
                att = await self.find_attachment(filename, messages)
                if not att:
                    return {
                        "result": {
                            "error": f'Image "{filename}" could not be found.',
                            "hint": 'Use an attachment in chat, for example, <attachment filename="image1.png"></attachment> would result in image1.png',
                        }
                    }
                attachments.append(att)

        images = None
        if attachments:
            normalize_attachments = getattr(gptimage, "normalize_attachments")
            images, original_resolution = await normalize_attachments(attachments)
            if not resolution:
                resolution = original_resolution
        elif not resolution:
            resolution = "1536x1024"

        async def callback():
            await asyncio.sleep(0)
            self.cog.currently_generating.discard(self.ctx.message.id)
        self.cog.currently_generating.add(self.ctx.message.id)
        generate_image = getattr(gptimage, "imagine")
        asyncio.create_task(generate_image(self.ctx, resolution=resolution, prompt=prompt, images=images, callback=callback()))

        return {
            "result": {
                "message": "Image generation started successfully. The user will have to wait for it to finish."
            }
        }
