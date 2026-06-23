import asyncio
import discord
from redbot.core import commands

from agent.utils import undo_xml
from agent.schema import ToolCall, Function, Parameters
from agent.tools.base import ToolBase


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
        gptimage: commands.Cog | None = self.ctx.bot.get_cog("GptImage")
        if not gptimage:
            return "<error>`gptimage` cog not installed, please notify the bot owner</error>"
        
        channel_mode = self.cog.config[self.ctx.guild].generation_channel_mode.value
        channels = self.cog.config[self.ctx.guild].generation_channels.value
        if channel_mode == "blacklist" and self.ctx.channel.id in channels \
                or channel_mode == "whitelist" and self.ctx.channel.id not in channels:
            if not self.ctx.channel.permissions_for(self.ctx.author).manage_messages:
                return "<error>Image generation is not allowed in this channel unless the user is a moderator</error>"

        prompt: str = undo_xml(arguments.get("prompt", "")).strip()
        aspect_ratio: str = arguments.get("resolution", "").lower().strip()
        references: list[str] = arguments.get("references") or []
        extra_references: list[str] = arguments.get("extra_references") or []
        base_image: str = arguments.get("base_image", "")
        existing = references + extra_references
        if base_image:
            existing.insert(0, base_image)

        if not prompt:
            return "<error>No prompt provided</error>"
        if not existing and "first image" in prompt or len(existing) < 2 and "second image" in prompt or len(existing) < 3 and "third image" in prompt:
            return "<error>You didn't provide all the reference images</error>"
    
        if base_image:
            prompt = f"Keep the image the same, except for the following changes: {prompt}"
        #elif existing:
        #    prompt += "\nMake an entirely new image, don't use the provided images as direct input."

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
            limit = self.cog.config[self.ctx.guild].backread_messages.value
            messages = [message async for message in self.ctx.channel.history(limit=limit+1)]
            if self.ctx.message and self.ctx.message.reference and self.ctx.message.reference.message_id:
                quoted = self.ctx.message.reference.cached_message or await self.ctx.channel.fetch_message(self.ctx.message.reference.message_id)
                messages.insert(0, quoted)
            for i, filename in enumerate(existing):
                att = await self.find_attachment(filename, messages, attachments)
                if not att:
                    return {
                        "result": {
                            "error": f'Image "{filename}" could not be found.',
                            "hint": 'Use an attachment in chat, for example, <attachment filename="image.png"></attachment> would result in image.png',
                        }
                    }
                attachments.append(att)

        images = None
        if attachments:
            normalize_attachments = getattr(gptimage, "normalize_attachments")
            images, original_resolution = await normalize_attachments(attachments)
            if base_image:
                resolution = original_resolution
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
                                       ' You must never describe a reference image ot its details or its filename,' \
                                       ' instead simply put it in the `references` field and refer to it by order (eg "the first image").'
                    },
                    "references": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 0,
                        "maxItems": 4,
                        "description": 'These will be used to help make the final image.'\
                                       ' Each must be the filename of individual chat message attachments, extracted from the message history only.'
                    },
                    "resolution": {
                        "type": "string",
                        "description": "Forces an aspect ratio for the image.",
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
                    "base_image": {
                        "type": "string",
                        "description": 'The filename of a chat message attachment, extracted from the message history only. Refer to this as "the first image".'
                    },
                    "prompt": {
                        "type": "string",
                        "description": 'A prompt AS SHORT AS POSSIBLE with ONLY the necessary changes to the first image.' \
                                       ' You must never describe the details of the images or their filenames in the prompt,' \
                                       ' instead you must put them in the `base_image` and `extra_references` fields and refer to them by their order.'
                    },
                    "extra_references": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 0,
                        "maxItems": 3,
                        "description": 'Use if more than one image is needed. Refer to these as "the second/third/fourth image".'\
                                       ' Each must be the filename of individual chat message attachments, extracted from the message history only.',
                    },
                },
                required=["base_image", "prompt"],
            )))
