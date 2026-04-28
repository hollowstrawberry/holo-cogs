import asyncio
import discord
from redbot.core import commands

from gptmemory.utils import undo_xml
from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.tools.base import ToolBase


class GptImageToolBase(ToolBase):
    async def find_attachment(self, filename: str, messages: list[discord.Message], consumed: list[discord.Attachment]) -> discord.Attachment | None:
        assert self.ctx.guild
        for message in messages:
            for attachment in message.attachments:
                if attachment.filename == filename and attachment not in consumed:
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

        prompt: str = undo_xml(arguments.get("prompt", ""))
        aspect_ratio: str = arguments.get("resolution", "")
        existing: list[str] = arguments.get("references") or []
        existing_single: str = arguments.get("image", "")
        if existing_single:
            existing.append(existing_single)

        if not prompt:
            return "<error>No prompt provided</error>"
        if not existing and "second image" in prompt:
            return "<error>You didn't provide all the reference images</error>"
        if existing_single:
            prompt = f"Keep the image the same, except for the following changes: {prompt}"

        gptimage: commands.Cog | None = self.ctx.bot.get_cog("GptImage")
        if not gptimage:
            return "<error>`gptimage` cog not installed, please notify the bot owner</error>"

        aspect_ratio = aspect_ratio.lower().strip()
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
            limit = await self.cog.config.guild(self.ctx.guild).backread_messages()
            messages = [message async for message in self.ctx.channel.history(limit=limit)]
            if self.ctx.message and self.ctx.message.reference and self.ctx.message.reference.message_id:
                quoted = self.ctx.message.reference.cached_message or await self.ctx.channel.fetch_message(self.ctx.message.reference.message_id)
                messages.insert(0, quoted)
            for i, filename in enumerate(existing):
                att = await self.find_attachment(filename, messages, attachments)
                if not att and not (i == 1 and attachments and attachments[0].filename == filename):  # try to prevent same image in both schema fields
                    return {
                        "result": {
                            "error": f'Image "{filename}" could not be found.',
                            "hint": 'Use an attachment in chat, for example, <attachment filename="image.png"></attachment> would result in image.png',
                        }
                    }
                if att:
                    attachments.append(att)

        images = None
        if attachments:
            normalize_attachments = getattr(gptimage, "normalize_attachments")
            images, new_resolution = await normalize_attachments(attachments)
            if not resolution:
                resolution = new_resolution
        if not resolution:
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


class GptImageGenTool(GptImageToolBase):
    display_name="gptimage_gen"
    schema = ToolCall(
        Function(
            name="generate_image",
            description="Generate an image with GPT, suitable for general/generic content.",
            parameters=Parameters(
                properties={
                    "prompt": {
                        "type": "string",
                        "description": 'A detailed prompt in natural language.' \
                                       ' You must never describe a reference image, instead put it in the `references` field.' \
                                       ' You can refer to them in the prompt by order (eg "the second image") and not by filename.'
                    },
                    "references": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 0,
                        "maxItems": 4,
                        "description": 'These will be used to help make the image.'\
                                       ' Each must be the filename of a chat message attachment, extracted from the message history only.' \
                                       ' Include all of them even if they have the same name.',
                    },
                    "resolution": {
                        "type": "string",
                        "description": "Optional. Forces an aspect ratio for the image.",
                        "enum": ["original", "square", "portrait", "landscape"]
                    },

                },
                required=["prompt"],
            )))
    

class GptImageEditTool(GptImageToolBase):
    display_name="gptimage_edit"
    schema = ToolCall(
        Function(
            name="edit_image",
            description="Edits an image with GPT, suitable for general/generic content.",
            parameters=Parameters(
                properties={
                    "image": {
                        "type": "string",
                        "description": 'The filename of a chat message attachment, extracted from the message history only.'
                    },
                    "prompt": {
                        "type": "string",
                        "description": 'A prompt as short as possible with only the necessary changes.'
                    },
                },
                required=["image", "prompt"],
            )))
