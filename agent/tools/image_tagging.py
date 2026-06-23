import logging
import asyncio
import discord
from redbot.core import commands

from agent.utils import get_filename, clean_tag, normalize_image
from agent.schema import ToolCall, Function, Parameters
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
                        "description": "The filename of a chat message attachment, extracted from the message history only."
                    },
                },
                required=["image"],
            )))

    async def find_image(self, filename: str) -> discord.Attachment | str | None:
        assert self.ctx.guild
        limit = self.cog.config[self.ctx.guild].backread_messages.value
        messages = [message async for message in self.ctx.channel.history(limit=limit)]
        if self.ctx.message and self.ctx.message.reference and self.ctx.message.reference.message_id:
            quoted = await self.ctx.channel.fetch_message(self.ctx.message.reference.message_id)
            messages.insert(0, quoted)
        for message in messages:
            for attachment in message.attachments:
                if attachment.filename == filename:
                    return attachment
            for embed in message.embeds:
                if embed.image and embed.image.url and filename == get_filename(embed.image.url):
                    return embed.image.url
        return None
    
    async def run(self, arguments: dict) -> str | dict:
        assert self.ctx.guild
        filename: str = arguments.get("image", "")
        if not filename:
            return "<error>No filename provided</error>"
        arcenciel: commands.Cog | None = self.ctx.bot.get_cog("Arcenciel")
        if not arcenciel:
            return "<error>`arcenciel` cog not installed, please notify the bot owner</error>"
        image_source = await self.find_image(filename.strip())
        if not image_source:
            return {
                "result": {
                    "error": f'Image "{filename}" could not be found.',
                    "hint": 'Use an attachment in chat, for example, <attachment filename="image.png"></attachment> would result in "image.png"',
                }
            }

        emoji = self.get_setting("tagging_emoji")
        asyncio.create_task(self.ctx.message.add_reaction(emoji))
        try:
            if isinstance(image_source, discord.Attachment):
                image_bytes = await image_source.read()
            else:
                async with self.cog.session.get(image_source) as response:
                    response.raise_for_status()
                    image_bytes = await response.read()
            max_resolution = self.cog.config[self.ctx.guild].max_image_resolution.value
            fp = await asyncio.to_thread(normalize_image, image_bytes, max_resolution**2)
            if not fp:
                return f"<error>The image appears to be corrupted or invalid</error>"
            tags = await arcenciel.api.interrogate(fp, filename.rsplit(".", 1)[0] + ".png")  # type: ignore
            return f"`{', '.join([clean_tag(tag) for tag in tags])}`"
        except Exception as error:
            log.exception("LLM autotag")
            return f"<error>{type(error).__name__}: {error}</error>"
