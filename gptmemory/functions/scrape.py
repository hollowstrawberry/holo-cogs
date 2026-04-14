import re
import logging
import asyncio
import aiohttp
import trafilatura
from typing import Awaitable, Callable, OrderedDict

from gptmemory.utils import parse_arcenciel_model
from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase
from gptmemory.constants import GITHUB_FILE_URL_PATTERN, ARCENCIEL_MODEL_URL_PATTERN

log = logging.getLogger("gptmemory.scrape")


class ScrapeFunctionCall(FunctionCallBase):
    settings = {"scrape_emoji": "🔗"}
    schema = ToolCall(
        Function(
            name="open_url",
            description="Opens a URL and returns its text-based content.",
            parameters=Parameters(
                properties={
                    "url": {
                        "type": "string",
                        "description": "The link to open",
                    }},
                required=["url"],
            )))

    headers = {
        "Cache-Control": "no-cache",
        "Referer": "https://www.google.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.custom_scrapers: dict[re.Pattern, Callable[[re.Match], Awaitable[str | dict]]] = OrderedDict({
            GITHUB_FILE_URL_PATTERN: self.scrape_github_file,
            ARCENCIEL_MODEL_URL_PATTERN: self.scrape_arcenciel_model,
        })

    async def run(self, arguments: dict) -> dict | str:
        url = arguments.get("url")
        if not url:
            return "<error>No URL provided</error>"
            
        emoji = await self.get_setting("scrape_emoji")
        asyncio.create_task(self.ctx.message.add_reaction(emoji))
        
        for pattern, method in self.custom_scrapers.items():
            if match := pattern.search(url):
                return await method(match)
        return await self.scrape_generic(url)

    async def scrape_generic(self, url: str) -> str:
        try:
            async with self.cog.session.get(url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=5)) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if "text" not in content_type:
                    return f"<error>Contents of {url} is not text</error>"
                text = await response.text()
                content = trafilatura.extract(text) or text
        except asyncio.TimeoutError:
            return "<error>Timed out.</error>"
        except aiohttp.ClientError as error:
            log.warning(f"Opening {url}: {type(error).__name__}: {error}")
            return f"<error>Failed to open URL ({type(error).__name__})</error>"
        return content or "<error>The page is empty.</error>"
    
    async def scrape_github_file(self, match: re.Match) -> str:
        user = match.group("user")
        repo = match.group("repo")
        branch = match.group("branch")
        path = match.group("path")
        url = f"https://raw.githubusercontent.com/{user}/{repo}/refs/heads/{branch}/{path}"
        return await self.scrape_generic(url)

    async def scrape_arcenciel_model(self, match: re.Match) -> dict | str:
        model_id = match.group("id")
        url = f"https://arcenciel.io/api/models/{model_id}"
        try:
            async with self.cog.session.get(url, headers=self.headers) as response:
                response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientError as error:
            log.warning(f"Opening {url}: {type(error).__name__}: {error}")
            return "<error>Failed to open URL</error>"
        
        return {"model": parse_arcenciel_model(data)}
