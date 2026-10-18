import asyncio
import logging

import aiohttp
import discord
from redbot.core import commands

from agent.constants import LORA_PATTERN
from agent.schema import Function, ImageGenParams, Parameters, ToolCall
from agent.tools.base import ToolBase
from agent.utils import add_xml_group, undo_xml

log = logging.getLogger("agent.arcenciel_image")


class ArcencielImageTool(ToolBase):
    # Keep the display name so existing enabled_functions configuration still works.
    display_name = "arcenciel_image"
    settings = {key: "" for key in ("checkpoint", "sampler", "scheduler", "steps", "cfg", "shift")}
    schema = ToolCall(
        Function(
            name="generate_stable_diffusion",
            description="Generate with MOSS, optionally placing subjects and text in Regional Boxes. "
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
        arcenciel: commands.Cog | None = self.ctx.bot.get_cog("Arcenciel")
        if not arcenciel:
            return "<error>`arcenciel` cog not installed, please notify the bot owner</error>"
        from arcenciel.moss import agent_settings, layout_from_metadata
        from arcenciel.utils import ImageGenError

        try:
            agent_settings(self.cog, self.ctx)
        except ValueError as error:
            return f"<error>{error}</error>"
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
        moss = {}
        if "boxes" in arguments and arguments["boxes"] != []:
            moss["layout"] = {
                "version": 1,
                "coordinateSpace": "normalized_1000",
                "canvas": "single",
                "items": arguments["boxes"],
            }
        sizes = {
            choice.name.lower(): tuple(map(int, choice.value.split("x"))) for choice in arcenciel._resolution_choices
        }
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
        if (width is None or height is None) and metadata.get("Size"):
            try:
                width, height = map(int, metadata["Size"].split("x"))
            except (ValueError, TypeError):
                width, height = None, None
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
            negative_prompt=undo_xml(
                arguments.get("negative_prompt", metadata.get("Negative Prompt", "") if "layout" in moss else "")
            ),
            width=width,
            height=height,
            seed=seed,
            moss=moss,
            loras=loras,
        )
        try:
            payload = await arcenciel.prepare_image_payload(self.ctx, params)
        except (ValueError, ImageGenError, aiohttp.ClientError, TimeoutError) as error:
            return f"<error>{error}</error>"
        message_content = f"Requested at {self.ctx.message.jump_url} by {self.ctx.author.mention}"

        async def callback():
            await asyncio.sleep(0)
            self.cog.currently_generating.discard(self.ctx.message.id)

        self.cog.currently_generating.add(self.ctx.message.id)
        asyncio.create_task(
            arcenciel.generate_image(
                self.ctx, params=params, payload=payload, message_content=message_content, callback=callback()
            )
        )
        result = {"message": "MOSS image generation started. Wait for completion before describing the result."}
        add_xml_group(result, warnings, "warnings")
        return {"result": result}
