import logging
import aiohttp

from gptmemory.utils import format_arcenciel_model
from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase

log = logging.getLogger("gptmemory.arcenciel")


class ArcencielFunctionCall(FunctionCallBase):
    schema = ToolCall(
        Function(
            name="search_models_arcenciel",
            description="Searches stable diffusion models and loras on Arc en Ciel.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string",
                        "description": "Search in model titles. Leave empty if searching all models from a user.",
                    },
                    "user": {
                        "type": "string",
                        "description": "Name of a user to search. If included, only models by this user will be shown.",
                    }
                },
                required=[],
            )))
    
    HEADERS = {
        "User-Agent": "holo-cogs/v1 (https://github.com/hollowstrawberry/holo-cogs);"
    }

    async def run(self, arguments: dict) -> str:
        query = arguments.get("query", "")
        user = arguments.get("user", None)
        found_user: int | None = None

        if user:
            params = {"q": user}
            try:
                async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                    async with session.get("https://arcenciel.io/api/users/search", params=params) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
            except aiohttp.ClientError as error:
                log.warning(f"Trying to grab user from Arc en Ciel: {type(error).__name__}: {error}")
                return "[Error trying to grab user from Arc en Ciel]"
            if data:
                found_user = data[0]['id']
            else:
                return "[User not found]"

        params = {"search": query}
        if found_user is not None:
            params["userId"] = found_user
        try:
            async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                async with session.get("https://arcenciel.io/api/models/search", params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except aiohttp.ClientError as error:
            log.warning(f"Trying to grab model from Arc en Ciel: {type(error).__name__}: {error}")
            return "[Error trying to grab model from Arc en Ciel]"
        
        if not data["data"]:
            return "[No results]"

        results = []
        for result in data["data"]:
            if not result.get('versions', None):
                continue
            results.append(f"[[[ {format_arcenciel_model(result)} ]]]")
        return '\n\n'.join(results)
