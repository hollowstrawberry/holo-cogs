import asyncio
import logging
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

import aiohttp
import discord

from agent.constants import LORA_PATTERN
from agent.schema import Function, ImageGenParams, Parameters, ToolCall
from agent.tools.base import ToolBase
from agent.utils import add_xml_group, undo_xml

log = logging.getLogger("agent.arcenciel_image")


@runtime_checkable
class MetadataScanner(Protocol):
    async def grab_metadata_dict(self, message: discord.Message) -> Mapping[str, object]: ...


def text_argument(arguments: Mapping[str, object], name: str, default: str = "") -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text.")
    return value


def dimensions(value: str) -> tuple[int, int]:
    width, height = value.split("x")
    return int(width), int(height)


class ArcencielImageTool(ToolBase):
    # Keep the display name so existing enabled_functions configuration still works.
    display_name = "arcenciel_image"
    settings = {key: "" for key in ("checkpoint", "sampler", "scheduler", "steps", "cfg", "shift")}
    schema = ToolCall(
        Function(
            name="generate_stable_diffusion",
            description="Generate with MOSS, Anima or Illustrious/NoobAI. "
            "MOSS supports boxes; can rerender a bot image.",
            parameters=Parameters(
                properties={
                    "existing": {
                        "type": "string",
                        "description": "Bot image filename: reuse seed, size and MOSS layout.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Scene prose; optional <lora:filename:weight> tags. No layout JSON or ||.",
                    },
                    "negative_prompt": {"type": "string", "description": "Optional unwanted visual details."},
                    "checkpoint": {
                        "type": "string",
                        "description": "moss/anima/illustrious/noobai or exact checkpoint; omit for default.",
                    },
                    "resolution": {"type": "string", "enum": ["square", "portrait", "landscape", "ultrawide"]},
                    "boxes": {
                        "type": "array",
                        "description": "MOSS placement; [] clears inherited layout.",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "label": {"type": "string", "enum": ["character", "object", "text"]},
                                "caption": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 2048,
                                },
                                "bbox": {
                                    "type": "array",
                                    "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                                    "minItems": 4,
                                    "maxItems": 4,
                                    "description": "[top,left,bottom,right], 0..1000.",
                                },
                                "text": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 1024,
                                    "description": "Exact lettering for text boxes.",
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

    async def find_attachment(self, filename: str) -> tuple[bool, discord.Message | None]:
        assert self.ctx.guild and self.ctx.guild.me
        assert isinstance(self.ctx.channel, (discord.TextChannel, discord.Thread))
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

    async def run(self, arguments: dict[str, object]) -> dict[str, object] | str:
        assert (
            self.ctx.guild
            and isinstance(self.ctx.author, discord.Member)
            and isinstance(self.ctx.channel, (discord.TextChannel, discord.Thread))
        )
        arcenciel = self.ctx.bot.get_cog("Arcenciel")
        if arcenciel is None:
            return "<error>`arcenciel` cog not installed, please notify the bot owner</error>"
        from arcenciel.arcenciel import Arcenciel

        if not isinstance(arcenciel, Arcenciel):
            return "<error>`arcenciel` cog not installed, please notify the bot owner</error>"
        from arcenciel.agent_models import FAMILIES, choose_lora, choose_model, lora_family_hint, lora_weight
        from arcenciel.moss import agent_settings, layout_from_metadata
        from arcenciel.utils import ImageGenError

        try:
            agent_settings(self.cog, self.ctx)
        except ValueError as error:
            return f"<error>{error}</error>"
        try:
            prompt = undo_xml(text_argument(arguments, "prompt"))
            negative_prompt = text_argument(arguments, "negative_prompt") if "negative_prompt" in arguments else None
            aspect = text_argument(arguments, "resolution").lower()
            existing = text_argument(arguments, "existing")
            checkpoint = text_argument(arguments, "checkpoint").strip()
            if checkpoint.lower() in FAMILIES:
                checkpoint = checkpoint.lower()
        except ValueError as error:
            return f"<error>{error}</error>"
        if not prompt.strip():
            return "<error>Provide a natural-language scene prompt.</error>"
        if len(prompt) > 32768:
            return "<error>The scene prompt is too long.</error>"
        if arcenciel.api is None:
            return "<error>The Arc en Ciel generator is unavailable.</error>"
        moss: dict[str, object] = {}
        if "boxes" in arguments and arguments["boxes"] != []:
            moss["layout"] = {
                "version": 1,
                "coordinateSpace": "normalized_1000",
                "canvas": "single",
                "items": arguments["boxes"],
            }
        sizes = {choice.name.lower(): dimensions(choice.value) for choice in arcenciel._resolution_choices}
        if aspect and aspect not in sizes:
            return "<error>Choose square, portrait, landscape or ultrawide.</error>"
        width, height = sizes.get(aspect, (None, None))
        sent_by_me, message = await self.find_attachment(existing) if existing else (False, None)
        metadata: Mapping[str, object] = {}
        warnings: list[str] = []
        if message and sent_by_me:
            from arcenciel.comfy import ComfyMetadataReader

            attachment = next((item for item in message.attachments if item.filename == existing), None)
            try:
                if attachment:
                    metadata = ComfyMetadataReader.from_bytes(await attachment.read()).as_dict()
            except (discord.HTTPException, OSError, ValueError):
                warnings.append("Image metadata is unavailable; using a fresh image recipe.")
            if not metadata:
                scanner = self.ctx.bot.get_cog("ImageScanner")
                if isinstance(scanner, MetadataScanner):
                    metadata = await scanner.grab_metadata_dict(message)
        elif existing:
            warnings.append("The original bot image was not found; a new image will be made.")
        size = metadata.get("Size")
        if (width is None or height is None) and isinstance(size, str):
            try:
                width, height = dimensions(size)
            except (ValueError, TypeError):
                width, height = None, None
        try:
            raw_seed = metadata.get("Seed", -1)
            seed = int(raw_seed) if isinstance(raw_seed, (str, int, float)) else -1
        except (ValueError, TypeError, OverflowError):
            seed = -1
        requested_loras: list[tuple[str, float]] = []
        try:
            for lora, name, weight in LORA_PATTERN.findall(prompt):
                prompt = prompt.replace(lora, "").strip()
                requested_loras.append((name, lora_weight(float(weight))))
        except ValueError as error:
            return f"<error>{error}</error>"
        if not requested_loras and sent_by_me:
            original_prompt = metadata.get("Prompt")
            if isinstance(original_prompt, str):
                try:
                    requested_loras.extend(
                        (name, lora_weight(float(weight))) for _, name, weight in LORA_PATTERN.findall(original_prompt)
                    )
                except ValueError as error:
                    return f"<error>{error}</error>"
        if len(requested_loras) > 4:
            return "<error>Provide at most four LoRAs.</error>"
        if negative_prompt is None:
            original_negative = metadata.get("Negative Prompt", "")
            if isinstance(original_negative, str) and original_negative:
                negative_prompt = original_negative
        try:
            options, catalog = await arcenciel.api.refresh_moss_profile()
            lora_candidates = [
                (name, weight, await arcenciel.api.find_lora_entries(name)) for name, weight in requested_loras
            ]
            inherited = metadata.get("Checkpoint") if sent_by_me else None
            inherited = inherited if isinstance(inherited, str) else ""
            default = self.cog.config[self.ctx.guild].image_model_default.value
            old_checkpoint = (self.cog.config.tool_settings.value or {}).get("checkpoint")
            if default == "moss" and isinstance(old_checkpoint, str) and old_checkpoint:
                default = old_checkpoint
            hints = {hint for name, _, entries in lora_candidates if (hint := lora_family_hint(entries, name))}
            if hints <= {"noobai", "illustrious"} and len(hints) > 1:
                hints = {"illustrious"}
            if len(hints) > 1 and not (checkpoint or inherited):
                raise ValueError("The requested LoRAs target different model families. Choose a checkpoint explicitly.")
            inferred_family = next(iter(hints), "") if not (checkpoint or inherited) else ""
            choice = choose_model(
                options, catalog, family=inferred_family, checkpoint=checkpoint,
                inherited=inherited, default=default,
            )
            if choice.family != "moss" and moss:
                raise ValueError("Regional Boxes are available only with MOSS.")
            if choice.family == "moss" and "boxes" not in arguments and sent_by_me:
                recovered = layout_from_metadata(metadata)
                if recovered:
                    moss = dict(recovered)
            resolved_loras = [
                f"<lora:{choose_lora(entries, name, choice).removesuffix('.safetensors')}:{weight:g}>"
                for name, weight, entries in lora_candidates
            ]
        except (ValueError, ImageGenError, aiohttp.ClientError, TimeoutError) as error:
            return f"<error>{error}</error>"
        params = ImageGenParams(
            prompt=prompt,
            negative_prompt=undo_xml(negative_prompt) if negative_prompt is not None else None,
            width=width,
            height=height,
            seed=seed,
            checkpoint=choice.name,
            moss=moss if choice.family == "moss" else None,
            loras=resolved_loras,
        )
        try:
            payload = await arcenciel.prepare_image_payload(self.ctx, params)
        except (ValueError, ImageGenError, aiohttp.ClientError, TimeoutError) as error:
            return f"<error>{error}</error>"
        message_content = f"Requested at {self.ctx.message.jump_url} by {self.ctx.author.mention}"

        async def callback() -> None:
            await asyncio.sleep(0)
            self.cog.currently_generating.discard(self.ctx.message.id)

        self.cog.currently_generating.add(self.ctx.message.id)
        asyncio.create_task(
            arcenciel.generate_image(
                self.ctx, params=params, payload=payload, message_content=message_content, callback=callback()
            )
        )
        result: dict[str, object] = {
            "message": f"{choice.name} image generation started. Wait for completion before describing the result."
        }
        add_xml_group(result, warnings, "warnings")
        return {"result": result}
