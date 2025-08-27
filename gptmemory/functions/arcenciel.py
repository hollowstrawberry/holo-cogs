import logging
import aiohttp
from typing import Optional

from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase

log = logging.getLogger("gptmemory.arcenciel")


class ArcencielFunctionCall(FunctionCallBase):
    schema = ToolCall(
        Function(
            name="search_models_arcenciel",
            description="Searches stable diffusion models on Arc en Ciel.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string",
                        "description": "Search in model titles. Leave empty if searching all models from a user.",
                    },
                    "user": {
                        "type": "string",
                        "description": "Name of a user to search, if included, only models by this user will be shown.",
                    }
                },
                required=["query"],
            )))
    
    HEADERS = {
        "User-Agent": "holo-cogs/v1 (https://github.com/hollowstrawberry/holo-cogs);"
    }

    async def run(self, arguments: dict) -> str:
        query = arguments.get("query", "")
        user = arguments.get("user", None)
        found_user: Optional[int] = None

        if user:
            url = f"https://arcenciel.io/api/users/search?q={user}"
            try:
                async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                    async with session.get(url) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
            except aiohttp.ClientError:
                log.exception("Trying to grab user from Arc en Ciel")
                return "Error trying to grab user from Arc en Ciel"
            if data:
                found_user = data[0]['id']
            else:
                return "[User not found]"

        url = f"https://arcenciel.io/api/models/search?search={query}"
        if found_user is not None:
            url += f"&userId={found_user}"
        try:
            async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except aiohttp.ClientError:
            log.exception("Trying to grab model from Arc en Ciel")
            return "Error trying to grab model from Arc en Ciel"
        
        if not data["data"]:
            return "[No results]"

        results = []
        for result in data["data"]:
            if not result.get('versions', None):
                continue
            latest_version = sorted(result['versions'], key=lambda v: v['id'], reverse=True)[0]
            results.append(f"[[[ [Model URL: https://arcenciel.io/models/{result['id']}] " +
                           f"[Model type: {result['type']}] " +
                           f"[Model uploader: {result['uploader']['username']}]" +
                           f"[Date updated: {latest_version['publishedAt']}]"
                           f"[Versions: {'/'.join(set(version['baseModel'] for version in result['versions']))}] " +
                           f"[Model name:] {result['title']} ]]]")
        return '\n'.join(results)
