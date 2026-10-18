import asyncio
import logging

import discord
from redbot.core import commands

from agent.constants import LORA_PATTERN, SD_IMAGEGEN_RESOLUTIONS
from agent.schema import Function, ImageGenParams, Parameters, ToolCall
from agent.tools.base import ToolBase
from agent.utils import add_xml_group, find_nearest_resolution, undo_xml

log = logging.getLogger("agent.arcenciel_image")

MOSS_INSTRUCTIONS = (
    "For Arc en Ciel image generation use generate_moss_image with MOSS SFT. "
    "Describe the complete scene in natural language: subjects, relationships, setting, style, then details. "
    "Use optional boxes only when placement or exact lettering matters. Coordinates are "
    "[top,left,bottom,right] from 0 to 1000; keep each subject inside its own positive-area box. "
    "For text use label=text, a visual caption and the literal text including line breaks. "
    "Use separate text boxes when each line needs a fixed position. "
    "Do not output layout JSON in the scene prompt, booru-only tag lists, old || region syntax, "
    "or choose Anima for this tool. Existing images are newly rendered from their recipe, not edited pixels. "
    "Never invent LoRA filenames; only use known MOSS-compatible resources."
)


class ArcencielImageTool(ToolBase):
    # Keep the display name so existing enabled_functions configuration still works.
    display_name = "arcenciel_image"
    settings = {"moss_checkpoint": "moss_image_sft_int8_convrot.safetensors"}
    schema = ToolCall(
        Function(
            name="generate_moss_image",
            description="Generate with MOSS SFT, optionally placing subjects and text in Regional Boxes. "
            "Supports anime, illustration and photography. Can make a new rendition of a bot image recipe.",
            parameters=Parameters(
                properties={
                    "existing": {
                        "type": "string",
                        "description": "Bot image filename: reuse its seed, size and MOSS layout for a new rendition.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Use prose for subjects, relationships, setting, style and details. "
                        "Do not put layout JSON or || separators here.",
                    },
                    "negative_prompt": {"type": "string", "description": "Optional unwanted visual details."},
                    "resolution": {"type": "string", "enum": ["square", "portrait", "landscape", "ultrawide"]},
                    "boxes": {
                        "type": "array",
                        "maxItems": 8,
                        "description": "Placement boxes. Omit for ordinary scenes; [] clears an existing layout.",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "label": {"type": "string", "enum": ["character", "object", "text"]},
                                "caption": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 2048,
                                    "description": "Appearance, pose and visual details for this region.",
                                },
                                "bbox": {
                                    "type": "array",
                                    "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                                    "minItems": 4,
                                    "maxItems": 4,
                                    "description": "[top,left,bottom,right]; top < bottom and left < right.",
                                },
                                "text": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 1024,
                                    "description": "Only for label=text: exact lettering and line breaks.",
                                },
                            },
                            "required": ["label", "caption", "bbox"],
                        },
                    },
                },
                required=["prompt"],
            ),
        )
    )

    async def find_attachment(self, filename: str) -> tuple[bool, (discord.Message | None)]:
        assert self.ctx.guild
        limit = self.cog.config[self.ctx.guild].backread_messages.value
        messages = [message async for message in self.ctx.channel.history(limit=limit)]
        if self.ctx.message and self.ctx.message.reference and self.ctx.message.reference.message_id:
            quoted = self.ctx.message.reference.cached_message or await self.ctx.channel.fetch_message(
                self.ctx.message.reference.message_id
            )
            messages.insert(0, quoted)
        for message in messages:
            for attachment in message.attachments:
                if attachment.filename == filename:
                    return (message.author.id == self.ctx.guild.me.id, message)
        return (False, None)

    async def run(self, arguments: dict) -> dict | str:
        assert (
            self.ctx.guild
            and isinstance(self.ctx.author, discord.Member)
            and isinstance(self.ctx.channel, (discord.TextChannel, discord.Thread))
        )
        channel_mode = self.cog.config[self.ctx.guild].generation_channel_mode.value
        channels = self.cog.config[self.ctx.guild].generation_channels.value
        if (
            channel_mode == "blacklist"
            and self.ctx.channel.id in channels
            or channel_mode == "whitelist"
            and self.ctx.channel.id not in channels
        ) and not self.ctx.channel.permissions_for(self.ctx.author).manage_messages:
            return "<error>Image generation is not allowed in this channel unless the user is a moderator</error>"
        arcenciel: commands.Cog | None = self.ctx.bot.get_cog("Arcenciel")
        if not arcenciel:
            return "<error>`arcenciel` cog not installed, please notify the bot owner</error>"
        from arcenciel.moss import MOSS_MODELS, layout_from_metadata, moss_options

        if not isinstance(arguments.get("prompt"), str) or not arguments["prompt"].strip():
            return "<error>Provide a natural-language scene prompt.</error>"
        if any(
            key in arguments and not isinstance(arguments[key], str)
            for key in ("negative_prompt", "resolution", "existing")
        ):
            return "<error>Prompt, negative prompt, resolution and existing filename must be text.</error>"
        prompt = undo_xml(arguments["prompt"])
        if len(prompt) > 32768:
            return "<error>The scene prompt is too long.</error>"
        try:
            moss = moss_options(arguments.get("boxes"))
        except ValueError as error:
            return f"<error>{error}</error>"
        checkpoint = self.get_setting("moss_checkpoint")
        if checkpoint not in MOSS_MODELS:
            return "<error>Configure moss_checkpoint with a supported MOSS SFT or Base INT8 model.</error>"
        sizes = {"square": (1024, 1024), "portrait": (832, 1216), "landscape": (1216, 832), "ultrawide": (1536, 640)}
        aspect = arguments.get("resolution", "").lower()
        if aspect and aspect not in sizes:
            return "<error>Choose square, portrait, landscape or ultrawide.</error>"
        width, height = sizes.get(aspect, (None, None))
        existing = arguments.get("existing", "")
        sent_by_me, message = await self.find_attachment(existing) if existing else (False, None)
        metadata = {}
        warnings = []
        if message and sent_by_me:
            from arcenciel.comfy import ComfyMetadataReader

            attachment = next((item for item in message.attachments if item.filename == existing), None)
            try:
                if attachment:
                    metadata = ComfyMetadataReader.from_bytes(await attachment.read()).as_dict()
            except (discord.HTTPException, OSError, ValueError):
                warnings.append("Image metadata is unavailable; using a fresh MOSS recipe.")
            if not metadata:
                scanner = self.ctx.bot.get_cog("ImageScanner")
                if scanner:
                    metadata = await scanner.grab_metadata_dict(message)
            if "boxes" not in arguments:
                moss = layout_from_metadata(metadata) or moss
        elif existing:
            warnings.append("The original bot image was not found; a new MOSS image will be made.")
        if width is None or height is None:
            try:
                width, height = map(int, metadata.get("Size", "1024x1024").split("x"))
                width, height = find_nearest_resolution((width, height), SD_IMAGEGEN_RESOLUTIONS)
            except (ValueError, TypeError):
                width, height = 1024, 1024
        try:
            seed = int(metadata.get("Seed", -1))
        except (ValueError, TypeError):
            seed = -1
        loras = []
        for lora, _, _ in LORA_PATTERN.findall(prompt):
            prompt = prompt.replace(lora, "").strip()
            loras.append(lora)
        params = ImageGenParams(
            prompt=prompt,
            negative_prompt=undo_xml(arguments.get("negative_prompt", metadata.get("Negative Prompt", ""))),
            width=width,
            height=height,
            checkpoint=checkpoint,
            seed=seed,
            steps=28,
            cfg=4,
            sampler="euler",
            scheduler="moss",
            moss=moss,
            loras=loras,
        )
        message_content = f"Requested at {self.ctx.message.jump_url} by {self.ctx.author.mention}"

        async def callback():
            await asyncio.sleep(0)
            self.cog.currently_generating.discard(self.ctx.message.id)

        self.cog.currently_generating.add(self.ctx.message.id)
        asyncio.create_task(
            arcenciel.generate_image(self.ctx, params=params, message_content=message_content, callback=callback())
        )
        result = {"message": "MOSS image generation started. Wait for completion before describing the result."}
        add_xml_group(result, warnings, "warnings")
        return {"result": result}
