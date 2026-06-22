import logging
import asyncio
import aiohttp
import discord

from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.tools.base import ToolBase

log = logging.getLogger("gptmemory.searchweb")

VOICE_ERROR = "<error>An error occured and voice could not be used.</error>"


class FinevoiceTool(ToolBase):
    display_name = "finevoice"
    apis = [("finevoice", "api_key")]
    settings = {
        "voice_emoji": "🗣️",
        "voice": "",
    }
    schema = ToolCall(
        Function(
            name="voice",
            description="Uses text-to-speech to send an audio in chat with your own voice.",
            parameters=Parameters(
                properties={
                    "text": {
                        "type": "string"
                    },
                },
                required=["text"],
            )))

    async def run(self, arguments: dict) -> str:
        api_key = (await self.ctx.bot.get_shared_api_tokens("finevoice")).get("api_key")
        if not api_key:
            log.error("finevoice api_key not found")
            return VOICE_ERROR
        
        if self.ctx.bot_permissions.add_reactions:
            emoji = self.get_setting("voice_emoji")
            asyncio.create_task(self.ctx.message.add_reaction(emoji))

        if not arguments.get("text"):
            log.error("llm did not provide text for tts")
            return "<error>You didn't send a text to convert into audio.</error>"

        payload = {
            "voice": self.get_setting("voice"),
            "text": arguments["text"],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            async with self.cog.session.post("https://apis.finevoice.ai/v1/audio/speech-synthesis", json=payload, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientError:
            log.exception("finevoice tool: Failed to get response from endpoint.")
            return VOICE_ERROR
        
        voice_result_url = data.get("url") or next(data.get("urls"), None)
        if not voice_result_url:
            log.error(f"finevoice tool: Response data does not contain necessary 'url' field. Response data:\n{data}")
            return VOICE_ERROR

        try:
            async with self.cog.session.get(voice_result_url) as response:
                response.raise_for_status()
                audio_data = await response.read()
        except aiohttp.ClientError:
            log.exception("finevoice tool: Failed to download result.")
            return VOICE_ERROR
        
        file = discord.File(audio_data, filename=f"{self.ctx.me.display_name} speaking.mp3")
        await self.ctx.reply(file=file, allowed_mentions=discord.AllowedMentions.none())

        return "<result>You sent an audio file with your voice in chat.</result>"
