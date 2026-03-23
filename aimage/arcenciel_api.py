import io
import re
import logging
from typing import List, Union

import aiohttp
import discord
from redbot.core import commands

from aimage.base import AImageBase
from aimage.constants import ADETAILER_ARGS
from aimage.schema import ImageGenParams
from aimage.helpers import clean_model, is_nsfw

log = logging.getLogger("red.holo-cogs.aimage")


class ArcEnCielAPI:
    def __init__(self, cog: AImageBase, endpoint: str, api_key: str):
        self.cog = cog
        self.endpoint = endpoint
        self.headers = {
            "x-api-key": api_key
        }
        self.session = aiohttp.ClientSession()

    async def update_autocomplete_cache(self) -> None:
        url = self.endpoint + "/generator/options"
        async with self.session.get(url, headers=self.headers) as response:
            data = await response.json()
            for key, model_names in data["models"].items():
                self.cog.autocomplete_cache[key] = {clean_model(name): name for name in model_names}
            for key in ["samplers", "schedulers"]:
                self.cog.autocomplete_cache[key] = {name: name for name in data["limits"][key]}
        # this endpoint returns loras while the other doesn't
        url = self.endpoint + "/generator/models"
        async with self.session.get(url, headers=self.headers) as response:
            data = await response.json()
            for key, models in data.items():
                self.cog.autocomplete_cache[key] = {clean_model(model["name"]): model["name"] for model in models}
            

    async def request_image(self,
                            context: Union[commands.Context, discord.Interaction],
                            params: ImageGenParams = None,
                            payload: dict = None,
                            ) -> dict:
        assert params or payload
        member = context.user if isinstance(context, discord.Interaction) else context.author
        assert isinstance(context.channel, discord.abc.Messageable) and isinstance(member, discord.Member)
        nsfw = is_nsfw(context.channel)
        payload = payload or await self.build_image_payload(params, member, nsfw)  # type: ignore
        url = self.endpoint + "/generator/jobs"
        async with self.session.post(url, json=payload, headers=self.headers) as response:
            r = await response.json()
        if r.get("error"):
            raise ValueError(r["error"])
        return r["job"]
    
    async def close_request(self, id: str):
        url = f"{self.endpoint}/generator/jobs/{id}"
        async with self.session.delete(url, headers=self.headers) as response:
            response.raise_for_status()

    async def fetch_queue(self) -> List[dict]:
        url = self.endpoint + "/generator/jobs"
        async with self.session.get(url, headers=self.headers) as response:
            r = await response.json()
        if r.get("error"):
            raise ValueError(r["error"])
        return r["jobs"]
    
    async def download_image(self, job_id: str) -> io.BytesIO:
        url = f"{self.endpoint}/generator/jobs/{job_id}/outputs/0/download"
        async with self.session.get(url, headers=self.headers) as response:
            b = await response.read()
        return io.BytesIO(b)
    
    async def build_image_payload(self, params: ImageGenParams, member: discord.Member, nsfw: bool) -> dict:
        config = self.cog.config

        if params.negative_prompt is None:
            params.negative_prompt = ""
            stock_negative_prompt = await config.negative_prompt()
            if stock_negative_prompt not in params.negative_prompt:
                if params.negative_prompt:
                    params.negative_prompt = f"{stock_negative_prompt}, {params.negative_prompt}"
                else:
                    params.negative_prompt = stock_negative_prompt

        if "masterpiece" not in params.prompt and "best quality" not in params.prompt:
            params.prompt = "masterpiece, best quality, " + params.prompt
        
        loras = []
        for lora in re.findall(r"(<lora:([^:]+):(\d+\.?\d*)>)", params.prompt + params.lora):
            tag, name, weight = lora
            loras.append({
                "name": name,
                "weight": weight,
            })
            params.prompt = params.prompt.replace(tag, "")

        payload = {
            "mode": "txt2img",
            "prompt": params.prompt,
            "negativePrompt": params.negative_prompt or await config.negative_prompt(),
            "modelName": params.checkpoint or await config.member(member).checkpoint() or await config.checkpoint() or "",
            "vaeName": params.vae or await config.vae(),
            "seed": params.seed,
            "steps": params.steps or await config.sampling_steps(),
            "cfg": params.cfg or await config.cfg(),
            "samplerName": params.sampler or await config.sampler(),
            "scheduler": params.scheduler or await config.scheduler(),
            "width": params.width or await config.width(),
            "height": params.height or await config.height(),
            "batchSize": 1,
            "extraSeed": params.subseed,
            "extraSeedStrength": params.subseed_strength,
            "loras": loras,
            "sfwMode": False,
        }
        if await config.adetailer():
            payload.update(ADETAILER_ARGS)

        return payload
