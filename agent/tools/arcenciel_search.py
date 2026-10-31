import logging
import asyncio
import aiohttp

from agent.utils import parse_arcenciel_model, add_xml_group, get_cog, debug
from agent.types import ToolCall, Function, Parameters
from agent.tools.base import ToolBase

log = logging.getLogger("agent.arcenciel_search")


class ArcencielTool(ToolBase):
    display_name = "arcenciel_search"
    required_cog="Arcenciel"
    settings = {
        "arcenciel_emoji": "📁",
        "arcenciel_timeout": "10",
        "arcenciel_model_results": "5",
        "arcenciel_model_description_chars": "1000",
        "arcenciel_model_trigger_chars": "1000",
    }
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

    async def run(self, arguments: dict) -> dict | str:
        try:
            from arcenciel.arcenciel import Arcenciel
        except ImportError:
            return "<error>`arcenciel` cog not installed, please notify the bot owner</error>"
        if not (arcenciel := get_cog(self.cog.bot, Arcenciel)):
            return "<error>`arcenciel` cog not installed or outdated, please notify the bot owner</error>"
        if not arcenciel.api or not arcenciel.api.session.headers.get("x-api-key"):
            return "<error>`arcenciel` cog not configured, please notify the bot owner</error>"

        if self.ctx.bot_permissions.add_reactions:
            emoji = self.get_setting("arcenciel_emoji")
            asyncio.create_task(self.ctx.message.add_reaction(emoji))

        query = arguments.get("query", "")
        user = arguments.get("user", None)

        limit = self.get_setting_int("arcenciel_model_results", 1, 20) or 5
        description_limit = self.get_setting_int("arcenciel_model_description_chars", 0, 10000)
        trigger_limit = self.get_setting_int("arcenciel_model_trigger_chars", 0, 10000)
        timeout = aiohttp.ClientTimeout(self.get_setting_int("arcenciel_timeout", 1, 1000) or 10)

        params = {
            "search": query,
            "limit": limit,
        }

        try:
            if user:
                async with arcenciel.api.session.get(
                    f"{arcenciel.api.endpoint}/users/search",
                    params={"q": user}, headers=self.headers, timeout=timeout,
                ) as response:
                    response.raise_for_status()
                    user_data: list[dict] = await response.json()
                found_user = next((item for item in user_data if item.get("username", "").casefold() == user.casefold()), None)
                if found_user is None:
                    return "No Arc en Ciel user with that exact username"
                params["userId"] = found_user["id"]

            async with arcenciel.api.session.get(
                f"{arcenciel.api.endpoint}/models/search",
                params=params, headers=self.headers, timeout=timeout,
            ) as response:
                response.raise_for_status()
                data: dict = await response.json()

        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            log.warning(f"Trying to contact Arc en Ciel: {debug(error)}")
            return "<error>Failed to contact Arc en Ciel</error>"

        models: list[dict] = data.get("data", [])
        if not models:
            return "No results"

        results = []
        for result in models:
            if not result.get("versions"):
                continue
            results.append(parse_arcenciel_model(result, description_limit=description_limit, trigger_limit=trigger_limit))
        
        result = {}
        add_xml_group(result, results, "models")
        return {"result": result}
