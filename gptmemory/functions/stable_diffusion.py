import logging
import asyncio
import discord
from typing import Any
from redbot.core import commands

from gptmemory.schema import ToolCall, Function, Parameters, ImageGenParams, ImageRegionalParams, SplitType
from gptmemory.constants import LORA_PATTERN
from gptmemory.functions.base import FunctionCallBase

log = logging.getLogger("gptmemory.stablediffusion")


class StableDiffusionFunctionCall(FunctionCallBase):
    schema = ToolCall(
        Function(
            name="generate_stable_diffusion",
            description="Generate an image with Stable Diffusion. Optionally, adjusts an existing image.",
            parameters=Parameters(
                properties={
                    "existing": {
                        "type": "string",
                        "description": "The filename of an existing image to revise."
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The prompt for image generation. Uses booru tags instead of sentences. " +
                                       "Can be split into left prompt and right prompt by putting || between them."
                    },
                    "negative_prompt": {
                        "type": "string",
                        "description": "Additional terms you don't want to appear in the image."
                    },
                    "resolution": {
                        "type": "string",
                        "description": "Aspect ratio for the image.",
                        "enum": ["square", "portrait", "landscape", "ultrawide"]
                    },
                },
                required=["prompt"],
            )))

    async def find_attachment(self, filename: str) -> tuple[bool, (discord.Message | None)]:
        assert self.ctx.guild
        limit = await self.cog.config.guild(self.ctx.guild).backread_messages()
        messages = [message async for message in self.ctx.channel.history(limit=limit)]
        if self.ctx.message and self.ctx.message.reference and self.ctx.message.reference.message_id:
            quoted = await self.ctx.channel.fetch_message(self.ctx.message.reference.message_id)
            messages.insert(0, quoted)
        for message in messages:
            for attachment in message.attachments:
                if attachment.filename == filename and self.ctx.guild:
                    return (message.author.id == self.ctx.guild.me.id, message)
        return (False, None)
    
    async def run(self, arguments: dict) -> str:
        assert self.ctx.guild and isinstance(self.ctx.author, discord.Member) and isinstance(self.ctx.channel, (discord.TextChannel, discord.Thread))
        
        channel_mode = await self.cog.config.guild(self.ctx.guild).generation_channel_mode()
        channels = await self.cog.config.guild(self.ctx.guild).generation_channels()
        if channel_mode == "blacklist" and self.ctx.channel.id in channels \
                or channel_mode == "whitelist" and self.ctx.channel.id not in channels:
            if not self.ctx.channel.permissions_for(self.ctx.author).manage_messages:
                return "[Error: Image generation is not allowed in this channel unless the user is a moderator]"

        existing: str = arguments.get("existing", "")
        prompt: str = arguments.get("prompt", "")
        negative_prompt_extra: str = arguments.get("negative_prompt", "")
        aspect_ratio: str = arguments.get("resolution", "")

        if not prompt:
            return "[Error: No prompt provided]"
        aimage: commands.Cog | None = self.ctx.bot.get_cog("AImage")
        if not aimage:
            return "[Error: `aimage` cog not installed, please notify the bot owner]"
        imagescanner: commands.Cog | None = self.ctx.bot.get_cog("ImageScanner")
        if not imagescanner:
            return "[Error: `imagescanner` cog not installed, please notify the bot owner]"

        loras = []
        for lora, name, _ in LORA_PATTERN.findall(prompt):
            prompt = prompt.replace(lora, "").strip()
            loras.append(name)
        
        regions = None
        if "||" in prompt:
            segments = prompt.split("||")
            if len(segments) != 2:
                return "[Error: The prompt was divided into regions but was not in the format left||right]"
            regions = ImageRegionalParams(segments[0].strip(), segments[1].strip(), SplitType.HORIZONTAL.value, 50)

        if aspect_ratio.lower() == "square":
            width, height = 1024, 1024
        elif aspect_ratio.lower() == "landscape":
            width, height = 1216, 832
        elif aspect_ratio.lower() == "portrait":
            width, height = 832, 1216
        elif aspect_ratio.lower() == "ultrawide":
            width, height = 1536, 640
        else:
            width, height = None, None

        if existing:
            sent_by_me, message = await self.find_attachment(existing)
        else:
            sent_by_me, message = False, None

        if message and sent_by_me:
            metadata: dict[str, Any] = await imagescanner.grab_metadata_dict(message) # type: ignore

            # add negative tags that weren't already in the existing negative prompt
            negative_prompt = metadata.get("Negative Prompt", "")
            if not negative_prompt:
                negative_prompt = negative_prompt_extra
            elif negative_prompt_extra:
                tags = [tag.strip() for tag in negative_prompt_extra.split(",")]
                for tag in tags:
                    if tag not in negative_prompt:
                        negative_prompt += f", {tag}"

            if width is None or height is None:
                width, height = [int(d) for d in metadata.get("Size", "1024x1024").split("x")]                

            params = ImageGenParams(
                prompt=prompt,
                negative_prompt=negative_prompt,
                cfg=float(metadata.get("CFG", metadata.get("Cfg", 5))),
                checkpoint=metadata.get("Model", ""),
                width=width,
                height=height,
                sampler=metadata.get("Sampler", ""),
                scheduler=metadata.get("Scheduler", ""),
                seed=int(metadata.get("Seed", -1)),
                subseed=int(metadata.get("Extra Seed", -1)),
                subseed_strength=float(metadata.get("Extra Seed Strength", 0)),
                steps=int(metadata.get("Steps", 30)),
                vae=metadata.get("VAE", metadata.get("Vae", "")),
                loras=loras,
                regions=regions,
            )
        else:
            # add negative tags that weren't already in the default negative prompt
            default_negative_prompt = await aimage.config.negative_prompt() # type: ignore
            negative_prompt = ""
            if negative_prompt_extra:
                tags = [tag.strip() for tag in negative_prompt_extra.split(",")]
                negative_prompt = ", ".join([tag.strip() for tag in tags if tag.strip() not in default_negative_prompt])

            if regions and (width is None or height is None):
                width, height = 1216, 832

            params = ImageGenParams(
                prompt=prompt,
                negative_prompt=negative_prompt or None,
                width=width,
                height=height,
                loras=loras,
                regions=regions,
            )

        message_content = f"Requested at {self.ctx.message.jump_url} by {self.ctx.author.mention}"
        asyncio.create_task(aimage.generate_image(self.ctx, params=params, message_content=message_content))  # type: ignore

        response = "[Image generation started successfully, the user will have to wait for it to finish]"
        if existing and not message:
            response += " [Warning: The original image was not found, so a new one will be made]"
        elif existing and not sent_by_me:
            response += " [Warning: Only images generated by the bot can be revised, so a new image will be made]"
        return response
