import io
import json
import logging
import asyncio
import aiohttp
import discord
import discord.http

from agent.utils import generate_waveform
from agent.schema import ToolCall, Function, Parameters
from agent.tools.base import ToolBase
from agent.constants import INCOMPLETE_EMOTE_PATTERN

log = logging.getLogger("agent.finevoice")

FINEVOICE_ENDPOINT = "https://apis.finevoice.ai/v1/audio/speech-synthesis"
VOICE_ERROR = "<error>An error occured and voice could not be used.</error>"


class FinevoiceTool(ToolBase):
    display_name = "finevoice"
    apis = [("finevoice", "api_key")]
    settings = {
        "voice_emoji": "🗣️",
        "voice": "",
        "voice_speed": "1.0",
        "voice_pitch": "0",
        "voice_temperature": "0.9",
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

    async def run(self, arguments: dict) -> str | dict:
        api_key = (await self.ctx.bot.get_shared_api_tokens("finevoice")).get("api_key")
        if not api_key:
            log.error("finevoice api_key not found")
            return VOICE_ERROR
        
        if self.ctx.bot_permissions.add_reactions:
            emoji = self.get_setting("voice_emoji")
            asyncio.create_task(self.ctx.message.add_reaction(emoji))

        if not arguments.get("text"):
            log.warning("llm did not provide text for tts")
            return "<error>You didn't send a text to convert into audio.</error>"

        text = arguments["text"]
        text = INCOMPLETE_EMOTE_PATTERN.sub(".", text)

        payload = {
            "voice": self.get_setting("voice"),
            "text": text,
            "speed": float(self.get_setting("voice_speed")),
            "pitch": float(self.get_setting("voice_pitch")),
            "temperature": float(self.get_setting("voice_temperature")),
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            async with self.cog.session.post(FINEVOICE_ENDPOINT, json=payload, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientError:
            log.exception("Failed to get finevoice response")
            return VOICE_ERROR
        
        voice_result_url = data.get("url") or next(iter(data.get("urls", [])), None)
        if not voice_result_url:
            log.error(f"Finevoice response does not contain necessary 'url' field. Response data:\n{data}")
            return VOICE_ERROR

        try:
            async with self.cog.session.get(voice_result_url) as response:
                response.raise_for_status()
                audio_data = await response.read()
        except aiohttp.ClientError:
            log.exception(f"Failed to download result from {voice_result_url}")
            return VOICE_ERROR

        try:
            waveform, duration = await generate_waveform(audio_data)
        except Exception:
            log.exception("Generating audio waveform")
            return VOICE_ERROR
            
        # undocumented discord api
        file = discord.File(io.BytesIO(audio_data))
        params = discord.http.handle_message_parameters(file=file)
        extra_params = discord.http.handle_message_parameters(
            flags=discord.MessageFlags(voice=True),
            message_reference=self.ctx.message.to_message_reference_dict(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        assert params.multipart and extra_params.payload
        extra_params.payload["attachments"] = [{
            "id": 0,
            "filename": "voice-message.ogg",
            "duration_secs": duration,
            "waveform": waveform,
        }]
        params.multipart[0]["value"] = json.dumps(extra_params.payload)
        await self.ctx.channel._state.http.send_message(self.ctx.channel.id, params=params)
        
        return {
            "noreply": True,
            "message": "A voice message was successfully sent in chat.",
        }
