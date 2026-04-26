import io
import logging
import asyncio
import discord
from PIL import Image
from redbot.core import commands

from gptmemory.utils import undo_xml, find_nearest_resolution, normalize_image
from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.constants import GPT_IMAGEGEN_RESOLUTIONS
from gptmemory.tools.base import ToolBase

log = logging.getLogger("gptmemory.gptimage")


class GptImageTool(ToolBase):
    display_name="gptimage"
    schema = ToolCall(
        Function(
            name="generate",
            description="Generate or edit an image with GPT. This should be used for general/generic content. "\
                        "For more specific content, you must prioritize other tools.",
            parameters=Parameters(
                properties={
                    "existing": {
                        "type": "string",
                        "description": "Optional. The filename of an existing image in chat to edit."
                    },
                    "prompt": {
                        "type": "string",
                        "description": "A prompt for image generation using natural language. " \
                                       "Making new images requires a detailed prompt. " \
                                       "Editing an existing image should use a short and simple prompt only with the necessary changes."
                    },
                    "resolution": {
                        "type": "string",
                        "description": "Optional. Aspect ratio for the image.",
                        "enum": ["square", "portrait", "landscape"]
                    },
                },
                required=["prompt"],
            )))

    async def find_attachment(self, filename: str) -> discord.Attachment | None:
        assert self.ctx.guild
        limit = await self.cog.config.guild(self.ctx.guild).backread_messages()
        messages = [message async for message in self.ctx.channel.history(limit=limit)]
        if self.ctx.message and self.ctx.message.reference and self.ctx.message.reference.message_id:
            quoted = self.ctx.message.reference.cached_message or await self.ctx.channel.fetch_message(self.ctx.message.reference.message_id)
            messages.insert(0, quoted)
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

        existing: str = arguments.get("existing", "")
        prompt: str = undo_xml(arguments.get("prompt", ""))
        aspect_ratio: str = arguments.get("resolution", "")

        if not prompt:
            return "<error>No prompt provided</error>"
        gptimage: commands.Cog | None = self.ctx.bot.get_cog("GptImage")
        if not gptimage:
            return "<error>`gptimage` cog not installed, please notify the bot owner</error>"

        if aspect_ratio.lower() == "square":
            resolution = "1024x1024"
        elif aspect_ratio.lower() == "portrait":
            resolution = "1024x1536"
        elif aspect_ratio.lower() == "landscape":
            resolution = "1536x1024"
        else:
            resolution = None

        if existing:
            attachment = await self.find_attachment(existing)
            if not attachment:
                return "<error>The image to edit couldn't be found</error>"
        else:
            attachment = None

        images = None
        if attachment:
            fp = io.BytesIO()
            await attachment.save(fp, seek_begin=True)
            if resolution is None:
                image = Image.open(fp)
                if image.size not in GPT_IMAGEGEN_RESOLUTIONS:
                    width, height = find_nearest_resolution(image.size, GPT_IMAGEGEN_RESOLUTIONS)
                    resolution = f"{width}x{height}"
                else:
                    resolution = f"{image.width}x{image.height}"
            image_bytes = await asyncio.to_thread(normalize_image, fp.getvalue(), 1536)
            images=[image_bytes]
        elif resolution is None:
            resolution = "1536x1024"

        async def callback():
            await asyncio.sleep(0)
            self.cog.currently_generating.discard(self.ctx.message.id)
        self.cog.currently_generating.add(self.ctx.message.id)
        generate_image = getattr(gptimage, "imagine")
        asyncio.create_task(generate_image(self.ctx, resolution=resolution, prompt=prompt, images=images, callback=callback()))

        obj = {"message": "Image generation started successfully. The user will have to wait for it to finish."}
        return {"result": obj}
