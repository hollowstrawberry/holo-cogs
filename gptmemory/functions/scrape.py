import logging
import aiohttp
import trafilatura

from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase

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

    async def run(self, arguments: dict) -> str:
        url = arguments["url"]

        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    content_type = response.headers.get('Content-Type', '').lower()
                    if 'text' not in content_type:
                        return f"Contents of {url} is not text/html"
                    content = trafilatura.extract(await response.text())
        except aiohttp.ClientError:
            log.warning(f"Opening {url}", exc_info=True)
            return f"Failed to open {url}"

        return f"[Contents of {url}:]\n{content}"
