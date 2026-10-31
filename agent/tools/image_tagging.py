import logging
import asyncio

from agent.types import ToolCall, Function, Parameters
from agent.utils import get_filename, clean_booru_tag, normalize_image, backread_with_quote, find_image_source, resolve_image_source, get_cog
from agent.tools.base import ToolBase

log = logging.getLogger("agent.imagetagger")


class ImageTaggingTool(ToolBase):
    display_name = "image_tagging"
    required_cog="Arcenciel"
    settings = {
        "tagging_emoji": "🖼️"
    }
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
        try:
            from arcenciel.arcenciel import Arcenciel
        except ImportError:
            return "<error>`arcenciel` cog not installed, please notify the bot owner</error>"
        if not (arcenciel := get_cog(self.cog.bot, Arcenciel)):
            return "<error>`arcenciel` cog not installed or outdated, please notify the bot owner</error>"
        if not arcenciel.api or not arcenciel.api.session.headers.get("x-api-key"):
            return "<error>`arcenciel` cog not configured, please notify the bot owner</error>"

        reference: str = arguments.get("image", "")
        if not reference:
            return "<error>No image provided</error>"

        if self.ctx.bot_permissions.add_reactions:
            emoji = self.get_setting("tagging_emoji")
            asyncio.create_task(self.ctx.message.add_reaction(emoji))
        
        messages = await backread_with_quote(self.ctx, self.cog.config[self.ctx.guild].backread_messages.value)
        image_source = find_image_source(reference, messages, [])
        if not image_source:
            return {
                "result": {
                    "error": f'Image "{reference}" could not be found.',
                    "hint": "Use an attachment filename or image URL from the conversation.",
                }
            }
            
        try:
            image_bytes = await resolve_image_source(image_source, self.cog.session)
            max_resolution = self.cog.config[self.ctx.guild].max_image_resolution.value
            fp = await asyncio.to_thread(normalize_image, image_bytes, max_resolution**2)
            if not fp:
                return f"<error>The image appears to be corrupted or invalid</error>"
            filename = get_filename(image_source.url or reference).rsplit(".", 1)[0] + ".png"
            tags = await arcenciel.api.interrogate(fp, filename)
            return f"`{', '.join([clean_booru_tag(tag) for tag in tags])}`"
        except Exception as error:
            log.exception("LLM autotag")
            return f"<error>{type(error).__name__}: {error}</error>"
