import logging
import asyncio
import discord
from redbot.core import commands

from agent.utils import add_xml_group, undo_xml, find_nearest_resolution
from agent.schema import ToolCall, Function, Parameters, ImageGenParams, ImageRegionalParams, SplitType
from agent.constants import LORA_PATTERN, SD_IMAGEGEN_RESOLUTIONS, PIPE_SEPARATOR_PATTERN
from agent.tools.base import ToolBase

log = logging.getLogger("agent.arcenciel_image")


class ArcencielImageTool(ToolBase):
    display_name="arcenciel_image"
    settings = {"enable_regional_prompt": ""}
    schema = ToolCall(
        Function(
            name="generate_stable_diffusion",
            description="Generate anime-style art with Stable Diffusion or revise an existing Stable Diffusion image.",
            parameters=Parameters(
                properties={
                    "existing": {
                        "type": "string",
                        "description": "The filename of an existing stable diffusion image in chat to revise."
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The prompt for image generation. Uses booru tags instead of sentences. " #+
                                       #"Can be split into left prompt and right prompt by putting || between them."
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
        limit = self.cog.config[self.ctx.guild].backread_messages.value
        messages = [message async for message in self.ctx.channel.history(limit=limit)]
        if self.ctx.message and self.ctx.message.reference and self.ctx.message.reference.message_id:
            quoted = self.ctx.message.reference.cached_message or await self.ctx.channel.fetch_message(self.ctx.message.reference.message_id)
            messages.insert(0, quoted)
        for message in messages:
            for attachment in message.attachments:
                if attachment.filename == filename and self.ctx.guild:
                    return (message.author.id == self.ctx.guild.me.id, message)
        return (False, None)
    
    async def run(self, arguments: dict) -> dict | str:
        assert self.ctx.guild and isinstance(self.ctx.author, discord.Member) and isinstance(self.ctx.channel, (discord.TextChannel, discord.Thread))
        
        channel_mode = self.cog.config[self.ctx.guild].generation_channel_mode.value
        channels = self.cog.config[self.ctx.guild].generation_channels.value
        if channel_mode == "blacklist" and self.ctx.channel.id in channels \
                or channel_mode == "whitelist" and self.ctx.channel.id not in channels:
            if not self.ctx.channel.permissions_for(self.ctx.author).manage_messages:
                return "<error>Image generation is not allowed in this channel unless the user is a moderator</error>"

        existing: str = arguments.get("existing", "")
        prompt: str = undo_xml(arguments.get("prompt", ""))
        negative_prompt_extra: str = undo_xml(arguments.get("negative_prompt", ""))
        aspect_ratio: str = arguments.get("resolution", "")

        if not prompt:
            return "<error>No prompt provided</error>"
        arcenciel: commands.Cog | None = self.ctx.bot.get_cog("Arcenciel")
        if not arcenciel:
            return "<error>`arcenciel` cog not installed, please notify the bot owner</error>"
        imagescanner: commands.Cog | None = self.ctx.bot.get_cog("ImageScanner")
        if not imagescanner:
            return "<error>`imagescanner` cog not installed, please notify the bot owner</error>"

        loras = []
        for lora, _, _ in LORA_PATTERN.findall(prompt):
            prompt = prompt.replace(lora, "").strip()
            loras.append(lora)

        regions = None
        if not self.get_setting("enable_regional_prompt"):
            prompt = PIPE_SEPARATOR_PATTERN.sub("\n", prompt)
        elif "||" in prompt:
            segments = prompt.split("||")
            if len(segments) != 2:
                return "<error>The prompt was divided into regions but was not in the format left||right</error>"
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
            metadata: dict[str, str] = await getattr(imagescanner, "grab_metadata_dict")(message)

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
                if (width, height) not in SD_IMAGEGEN_RESOLUTIONS:
                    width, height = find_nearest_resolution((width, height), SD_IMAGEGEN_RESOLUTIONS)

            params = ImageGenParams(
                prompt=prompt,
                negative_prompt=negative_prompt,
                cfg=float(metadata.get("CFG", metadata.get("Cfg", 5))),
                checkpoint=metadata.get("Model") or metadata.get("Checkpoint") or "",
                width=width,
                height=height,
                sampler=metadata.get("Sampler", ""),
                scheduler=metadata.get("Scheduler", ""),
                seed=int(metadata.get("Seed", -1)),
                subseed=int(metadata.get("Extra Seed", -1)),
                subseed_strength=float(metadata.get("Extra Seed Strength", 0)),
                steps=int(metadata.get("Steps", 30)),
                vae=metadata.get("VAE") or metadata.get("Vae") or "",
                loras=loras,
                regions=regions,
            )
        else:
            # add negative tags that weren't already in the default negative prompt
            default_negative_prompt = await arcenciel.config.negative_prompt() # type: ignore
            negative_prompt = ""
            if negative_prompt_extra:
                tags = [tag.strip() for tag in negative_prompt_extra.split(",")]
                negative_prompt = ", ".join([tag.strip() for tag in tags if tag.strip() not in default_negative_prompt])

            if width is None or height is None:
                width, height = await self.cog.find_last_sd_generated_image_resolution(self.ctx)
            if regions and (width is None or height is None):
                width, height = 1216, 832

            params = ImageGenParams(
                prompt=prompt,
                negative_prompt=negative_prompt or None,
                width=width,
                height=height,
                loras=loras,
                regions=regions,
                checkpoint=self.get_setting("checkpoint"),
                sampler=self.get_setting("sampler"),
                scheduler=self.get_setting("scheduler"),
            )

        message_content = f"Requested at {self.ctx.message.jump_url} by {self.ctx.author.mention}"
        async def callback():
            await asyncio.sleep(0)
            self.cog.currently_generating.discard(self.ctx.message.id)
        self.cog.currently_generating.add(self.ctx.message.id)
        generate_image = getattr(arcenciel, "generate_image")
        asyncio.create_task(generate_image(self.ctx, params=params, message_content=message_content, callback=callback()))

        obj = {"message": "Image generation started successfully. The user will have to wait for it to finish."}
        warnings = []
        if existing and not message:
            warnings.append("The original image was not found, so a new one will be made.")
        elif existing and not sent_by_me:
            warnings.append("Only images generated by the bot can be revised, so a new image will be made.")
        add_xml_group(obj, warnings, "warnings")
        return {"result": obj}
