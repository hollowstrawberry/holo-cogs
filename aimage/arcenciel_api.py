import json
import logging
import aiohttp
from io import BytesIO

from aimage.base import AImageBase
from aimage.utils import ImageGenError, clean_model, parse_loras

log = logging.getLogger("red.holo-cogs.aimage")


class ArcEnCielAPI:
    def __init__(self, cog: AImageBase, endpoint: str, api_key: str):
        self.cog = cog
        self.endpoint = endpoint
        self.session = aiohttp.ClientSession(headers={"x-api-key": api_key})

    async def update_autocomplete_cache(self) -> None:
        url = self.endpoint + "/generator/options"
        async with self.session.get(url) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            data = await response.json()
        for key, model_names in data["models"].items():
            self.cog.autocomplete_cache[key] = {clean_model(name): name for name in model_names}
        for key in ["samplers", "schedulers"]:
            self.cog.autocomplete_cache[key] = {name: name for name in data.get("limits", {}).get(key, [])}

    async def request_image(self, payload: dict) -> dict:
        parse_loras(payload)
        url = self.endpoint + "/generator/jobs"
        async with self.session.post(url, json=payload) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            r = await response.json()
        return r["job"]
    
    async def close_request(self, id: str):
        url = f"{self.endpoint}/generator/jobs/{id}"
        async with self.session.delete(url) as response:
            if response.status >= 400:
                log.info(f"job {id} couldn't be deleted {response.status}")
            else:
                log.info(f"job {id} deleted")

    async def fetch_queue(self) -> list[dict]:
        url = self.endpoint + "/generator/jobs"
        async with self.session.get(url) as response:
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
        async with self.session.get(url, params=params) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            r = await response.json()
        return [lora["name"] for lora in r["entries"]]
    
    async def download_image(self, job_id: str) -> bytes:
        url = f"{self.endpoint}/generator/jobs/{job_id}/outputs/0/download"
        async with self.session.get(url) as response:
            if response.status >= 400 or "json" in response.content_type:
                raise ImageGenError(await self._extract_error(response))
            b = await response.read()
        return b
    
    async def upload_image(self, image: bytes, filename: str) -> str:
        url = self.endpoint + "/generator/uploads"
        data = aiohttp.FormData()
        data.add_field("image", image, filename=filename, content_type=f"image/{filename.split('.')[-1]}")
        data.add_field("kind", "REDBOT")
        async with self.session.post(url=url, data=data) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            r = await response.json()
        return r["path"]
    
    async def search_resource(self, query: str, *, hash_only: bool = False) -> list[dict]:
        if not query.strip():
            return []
        url = self.endpoint + "/models/search"
        params = {
            "search": query,
            "limit": 10,
        }
        if hash_only:
            params["hashOnly"] = "1"
        async with self.session.get(url, params=params) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            data = await response.json()
        return data["data"]
    
    async def interrogate(self, image: BytesIO, filename: str) -> list[str]:
        url = self.endpoint + "/generator/autotag/interrogate"
        data = aiohttp.FormData()
        image.seek(0)
        data.add_field("image", image, filename=filename, content_type=f"image/{filename.split('.')[-1]}")
        data.add_field("kind", "tagger")
        async with self.session.post(url=url, data=data) as response:
            if response.status >= 400:
                raise ImageGenError(await self._extract_error(response))
            r = await response.json()
        tags = r.get("tags", [])
        tags.insert(0, r.get("rating", "unknown_rating"))
        return tags

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
