import asyncio
import aiohttp
import discord
from typing import Coroutine, Protocol, runtime_checkable
from redbot.core import commands

from agent.utils import undo_xml, get_cog, backread_with_quote, find_image_source, read_image_source
from agent.types import ToolCall, Function, Parameters, ImageSource
from agent.tools.base import ToolBase


@runtime_checkable
class GptImage(Protocol):
    @staticmethod
    async def normalize_images(data: list[bytes]) -> tuple[list[bytes], str]: ...

    async def imagine(self, ctx: commands.Context, resolution: str, prompt: str, images: list[bytes], callback: Coroutine | None = None): ...


class GptImageToolBase(ToolBase):
    imagegen = True

    async def run(self, arguments: dict) -> dict | str:
        assert self.ctx.guild and isinstance(self.ctx.author, discord.Member) and isinstance(self.ctx.channel, (discord.TextChannel, discord.Thread))
        gptimage = get_cog(self.cog.bot, GptImage)
        if not gptimage:
            return "<error>`gptimage` cog missing or outdated, please notify the bot owner</error>"
        
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
        if len(existing) > 4:
            return "<error>At most four input images are supported</error>"
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

        sources: list[ImageSource] = []
        if existing:
            limit = self.cog.config[self.ctx.guild].backread_messages.value
            messages = await backread_with_quote(self.ctx, limit)
            for reference in existing:
                source = find_image_source(reference, messages, sources)
                if not source:
                    return {
                        "result": {
                            "error": f'Image "{reference}" could not be found or was already selected.',
                            "hint": "Use an attachment filename or image URL from the conversation.",
                        }
                    }
                sources.append(source)

        images: list[bytes] = []
        if sources:
            try:
                data = [await read_image_source(source, self.cog.session) for source in sources]
                images, original_resolution = await gptimage.normalize_images(data)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
                return "<error>Could not read an input image. It may be unavailable, invalid or too large.</error>"
            if base_image:
                resolution = original_resolution
        if not resolution:
            resolution = "1536x1024"

        async def callback():
            await asyncio.sleep(0)
            self.cog.currently_generating.discard(self.ctx.message.id)
        self.cog.currently_generating.add(self.ctx.message.id)
        
        asyncio.create_task(
            gptimage.imagine(self.ctx, resolution=resolution, prompt=prompt, images=images, callback=callback())
        )

        return {
            "result": {
                "message": "Image generation started successfully. The user will have to wait for it to finish."
            }
        }


class GptImageGenTool(GptImageToolBase):
    display_name="gptimage_gen"
    schema = ToolCall(
        Function(
            name="generate_image_gpt",
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
                        "description": 'Reference images: attachment filenames or image URLs from the conversation.'
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
                        "description": 'Attachment filename or image URL from the conversation. Refer to this as "the first image".'
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
                        "description": 'Additional attachment filenames or image URLs from the conversation, in reference order.',
                    },
                },
                required=["base_image", "prompt"],
            )))
