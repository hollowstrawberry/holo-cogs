import logging
import asyncio
import discord
from redbot.core import commands

from agent.utils import get_filename, clean_tag, normalize_image, image_messages, find_image_source, read_image_source
from agent.types import ToolCall, Function, Parameters
from agent.tools.base import ToolBase

log = logging.getLogger("agent.imagetagger")


class ImageTaggingTool(ToolBase):
    display_name = "image_tagging"
    settings = {"tagging_emoji": "🖼️"}
    schema = ToolCall(
        Function(
            name="infer_booru_tags",
            description="Determine the booru tags that would match a user-provided image. Typically only useful for stable diffusion prompts.",
            parameters=Parameters(
                properties={
                    "image": {
                        "type": "string",
                        "description": "Attachment filename or image URL from the conversation."
                    },
                },
                required=["image"],
            )))

    async def run(self, arguments: dict) -> str | dict:
        assert self.ctx.guild
        reference: str = arguments.get("image", "")
        if not reference:
            return "<error>No image provided</error>"
        arcenciel: commands.Cog | None = self.cog.bot.get_cog("Arcenciel")
        if not arcenciel:
            return "<error>`arcenciel` cog not installed, please notify the bot owner</error>"
        messages = await image_messages(self.ctx, self.cog.config[self.ctx.guild].backread_messages.value)
        image_source = find_image_source(reference, messages, [])
        if not image_source:
            return {
                "result": {
                    "error": f'Image "{reference}" could not be found.',
                    "hint": "Use an attachment filename or image URL from the conversation.",
                }
            }

        emoji = self.get_setting("tagging_emoji")
        asyncio.create_task(self.ctx.message.add_reaction(emoji))
        try:
            image_bytes = await read_image_source(image_source, self.cog.session)
            max_resolution = self.cog.config[self.ctx.guild].max_image_resolution.value
            fp = await asyncio.to_thread(normalize_image, image_bytes, max_resolution**2)
            if not fp:
                return f"<error>The image appears to be corrupted or invalid</error>"
            filename = get_filename(image_source.url or reference).rsplit(".", 1)[0] + ".png"
            tags = await arcenciel.api.interrogate(fp, filename)  # type: ignore
            return f"`{', '.join([clean_tag(tag) for tag in tags])}`"
        except Exception as error:
            log.exception("LLM autotag")
            return f"<error>{type(error).__name__}: {error}</error>"
