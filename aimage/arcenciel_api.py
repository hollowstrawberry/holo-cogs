import json
import logging
import os
import aiohttp
import discord
from copy import deepcopy
from redbot.core import commands

from aimage.base import AImageBase
from aimage.comfy import ComfyMetadata
from aimage.utils import ImageGenError, clean_model, parse_loras
from aimage.schema import ImageGenParams
from aimage.constants import ADETAILER_ARGS, RESOURCE_HASH_REGEX

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
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            data = await response.json()
        for key, model_names in data["models"].items():
            self.cog.autocomplete_cache[key] = {clean_model(name): name for name in model_names}
        for key in ["samplers", "schedulers"]:
            self.cog.autocomplete_cache[key] = {name: name for name in data.get("limits", {}).get(key, [])}

    async def request_image(self,
                            context: commands.Context | discord.Interaction,
                            payload: dict,
                            ) -> dict:
        member = context.user if isinstance(context, discord.Interaction) else context.author
        assert isinstance(context.channel, discord.abc.Messageable) and isinstance(member, discord.Member)
        parse_loras(payload)
        url = self.endpoint + "/generator/jobs"
        async with self.session.post(url, json=payload, headers=self.headers) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            r = await response.json()
        return r["job"]
    
    async def close_request(self, id: str):
        url = f"{self.endpoint}/generator/jobs/{id}"
        async with self.session.delete(url, headers=self.headers) as response:
            if response.status >= 400:
                log.info(f"job {id} couldn't be deleted {response.status}")
            else:
                log.info(f"job {id} deleted")

    async def fetch_queue(self) -> list[dict]:
        url = self.endpoint + "/generator/jobs"
        async with self.session.get(url, headers=self.headers) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            r = await response.json()
        return r["jobs"]

    async def search_loras(self, query: str) -> list[str]:
        url = self.endpoint + "/generator/models/loras"
        params = {
            "q": query,
            "limit": 25,
        }
        async with self.session.get(url, params=params, headers=self.headers) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            r = await response.json()
        return [lora["name"] for lora in r["entries"]]
    
    async def download_image(self, job_id: str) -> bytes:
        url = f"{self.endpoint}/generator/jobs/{job_id}/outputs/0/download"
        async with self.session.get(url, headers=self.headers) as response:
            if response.status >= 400 or "json" in response.content_type:
                raise ImageGenError(await self._extract_error(response))
            b = await response.read()
        return b
    
    async def upload_image(self, image: bytes, filename: str) -> str:
        url = self.endpoint + "/generator/uploads"
        data = aiohttp.FormData()
        data.add_field("image", image, filename=filename, content_type=f"image/{filename.split('.')[-1]}")
        data.add_field("kind", "REDBOT")
        async with self.session.post(url=url, data=data, headers=self.headers) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            r = await response.json()
        return r["path"]
    
    async def search_resource(self, query: str, *, hash_only: bool = False, limit: int = 10) -> list[dict]:
        if not query.strip():
            return []
        url = self.endpoint + "/models/search"
        params: dict[str, str] = {
            "search": query,
            "limit": str(limit),
        }
        if hash_only:
            params["hashOnly"] = "1"
        async with self.session.get(url, params=params, headers=self.headers) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            data = await response.json()
        return data["data"]
    
    async def interrogate(self, image: bytes, filename: str) -> list[str]:
        url = self.endpoint + "/generator/autotag/interrogate"
        data = aiohttp.FormData()
        data.add_field("image", image, filename=filename, content_type=f"image/{filename.split('.')[-1]}")
        data.add_field("kind", "tagger")
        async with self.session.post(url=url, data=data, headers=self.headers) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            r = await response.json()
        tags = r.get("tags", [])
        tags.insert(0, r.get("rating", "unknown_rating"))
        return tags
        
    async def build_image_payload(self, params: ImageGenParams, member: discord.Member, nsfw: bool) -> dict:
        config = self.cog.config

        stock_negative_prompt = await config.negative_prompt()
        if stock_negative_prompt not in (params.negative_prompt or ""):
            if params.negative_prompt:
                params.negative_prompt = f"{stock_negative_prompt}, {params.negative_prompt}"
            else:
                params.negative_prompt = stock_negative_prompt
        
        checkpoint = params.checkpoint or await config.user(member).checkpoint() or await config.checkpoint() or ""
        vae = params.vae or await config.vae()
        loras = []
        for lora in params.loras:
            loras.append({
                "name": f"{lora.replace('.safetensors', '')}.safetensors",
                "weight": 1.0,
            })

        payload = {
            "mode": "img2img" if params.image else "txt2img",
            "prompt": params.prompt,
            "negativePrompt": params.negative_prompt or await config.negative_prompt(),
            "modelName": checkpoint.replace(".safetensors", "") + ".safetensors",
            "vaeName": vae.replace(".safetensors", "") + ".safetensors" if vae else None,
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
            "sfwMode": not nsfw,
        }

        if params.image:
            if params.image.denoising is not None:
                payload["denoise"] = params.image.denoising
            if params.image.scale is not None:
                payload["scaleFactor"] = params.image.scale

        if params.regions:
            payload["attentionCouple"] = {
                "enabled": True,
                "layoutPreset": params.regions.split_type.value,
                "splitPercent": params.regions.split_percent,
                "globalPromptWeight": 0.3,
                "regions": [
                    {"prompt": params.regions.prompt1, "weight": 1, "maskPath": None,},
                    {"prompt": params.regions.prompt2, "weight": 1, "maskPath": None,},
                ],
            }

        if await config.adetailer():
            payload["adetailer"] = deepcopy(ADETAILER_ARGS)
        
        return payload

    async def _extract_error(self, response: aiohttp.ClientResponse) -> str:
        try:
            data = await response.json()
        except json.JSONDecodeError:
            return await response.text()
        for key in ("error", "message", "reason"):
            if data.get(key):
                return str(data[key]).strip()
        if data.get("errorCode"):
            return str(data["errorCode"]).strip()
        return f"HTTP {response.status}"
