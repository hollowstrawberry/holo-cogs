import re
import logging
import aiohttp
import trafilatura
from typing import Awaitable, Callable, Dict, OrderedDict

from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase
from gptmemory.constants import GITHUB_FILE_URL_PATTERN, ARCENCIEL_MODEL_URL_PATTERN

log = logging.getLogger("gptmemory.scrape")


class ScrapeFunctionCall(FunctionCallBase):
    schema = ToolCall(
        Function(
            name="open_url",
            description="Opens a URL and returns its contents. Does not support non-text content types.",
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
        self.custom_scrapers: Dict[re.Pattern, Callable[[re.Match], Awaitable[str]]] = OrderedDict({
            GITHUB_FILE_URL_PATTERN: self.scrape_github_file,
            ARCENCIEL_MODEL_URL_PATTERN: self.scrape_arcenciel_model,
        })

    async def run(self, arguments: dict) -> str:
        url = arguments["url"]
        for pattern, method in self.custom_scrapers.items():
            if match := pattern.search(url):
                return await method(match)
        else:
            return await self.scrape_generic(url)

    async def scrape_generic(self, url: str) -> str:
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    content_type = response.headers.get('Content-Type', '').lower()
                    if 'text' not in content_type:
                        return f"[Contents of {url} is not text]"
                    text = await response.text()
                    content = trafilatura.extract(text) or text                        
        except aiohttp.ClientError:
            log.warning(f"Opening {url}", exc_info=True)
            return f"[Failed to open URL]"
        
        return content
    
    async def scrape_github_file(self, match: re.Match) -> str:
        user = match.group("user")
        repo = match.group("repo")
        branch = match.group("branch")
        path = match.group("path")
        url = f"https://raw.githubusercontent.com/{user}/{repo}/refs/heads/{branch}/{path}"
        return await self.scrape_generic(url)

    async def scrape_arcenciel_model(self, match: re.Match) -> str:
        model_id = match.group("id")
        url = f"https://arcenciel.io/api/models/{model_id}"
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    data = await response.json()
        except aiohttp.ClientError:
            log.warning(f"Opening {url}", exc_info=True)
            return f"[Failed to open URL]"
        
        description = trafilatura.extract(data['description']) or data['description'] or "(Empty)"
        versions = sorted(data.get("versions", []), key=lambda v: v['id'], reverse=True)
        model_info = f"[[ Model name: {data['title']} ]] [Type: {data['type']}] [Uploader: {data['uploader']['username']}] [Versions: {len(versions)}]"
        versions_info = ""
        for i, version in enumerate(versions):
            versions_info += f"\n[[ [Version name: {version['versionName']}] [Base model: {version['baseModel']}] [Published: {version['publishedAt']}]"
            if i == 0 and version.get('activationTags', []):
                versions_info += " [Activation tags:] " + " ".join(f"[{tags}]" for tags in version['activationTags'])
                for tags in version['activationTags']:
                    if tags in description:
                        description = description.replace(tags, "[tags]")
            versions_info += " ]]"
        content = f"{model_info} [Model description:] {description}\n{versions_info}"
        return content
