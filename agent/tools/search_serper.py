import json
import logging
import asyncio
import aiohttp

from agent.utils import add_xml_group
from agent.types import ToolCall, Function, Parameters
from agent.tools.base import ToolBase

log = logging.getLogger("agent.searchgoogle")


class SerperSearchTool(ToolBase):
    display_name = "serper_search"
    apis = [("serper", "api_key")]
    settings = {
        "search_emoji": "🌐",
        "search_timeout": "10",
    }
    schema = ToolCall(
        Function(
            name="search_google",
            description="Googles a query for any unknown information or for updates on old information. Uses serper api.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    }},
                required=["query"],
            )))

    async def run(self, arguments: dict) -> dict | str:
        api_key = (await self.ctx.bot.get_shared_api_tokens("serper")).get("api_key")
        if not api_key:
            log.error("Tried to do a google search but serper api_key not found")
            return "<error>An error occured while searching Google.</error>"

        if self.ctx.bot_permissions.add_reactions:
            emoji = self.get_setting("search_emoji")
            asyncio.create_task(self.ctx.message.add_reaction(emoji))

        url = "https://google.serper.dev/search"
        query = arguments["query"]
        payload = json.dumps({"q": query})
        headers = {'X-API-KEY': api_key, 'Content-Type': 'application/json'}
        timeout = aiohttp.ClientTimeout(self.get_setting_int("search_timeout", 1, 1000) or 10)
        try:
            async with self.cog.session.post(url, data=payload, headers=headers, timeout=timeout) as response:
                response.raise_for_status()
                data: dict = await response.json()
        except aiohttp.ClientError as error:
            log.warning(f"Failed request to serper.io: {type(error).__name__}: {error}")
            return "<error>An error occured while searching Google.</error>"

        obj = {}
        if answer_box := data.get("answerBox", {}):
            if "title" in answer_box and "answer" in answer_box:
                obj["question"] = answer_box["title"]
                obj["answer"] = answer_box["answer"]
            if "source" in answer_box:
                obj["source"] = answer_box["source"]
            if "snippet" in answer_box:
                obj["snippet"] = answer_box["snippet"]

        if graph := data.get("knowledgeGraph", {}):
            if "title" in graph:
                obj["title"] = graph["title"]
            if "type" in graph:
                obj["type"] = graph["type"]
            if "description" in graph:
                obj["description"] = graph["description"]
            if "website" in graph:
                obj["website"] = graph["website"]
            attributes = []
            for attribute, value in graph.get("attributes", {}).items():
                attributes.append({
                    "@name": attribute,
                    "#text": value,
                })
            add_xml_group(obj, attributes, "attributes")

        if organic_results := data.get("organic", []):
            obj["first_result_url"] = organic_results[0]["link"]
            obj["first_result_snippet"] = organic_results[0]["snippet"]

        if len(obj) == 0:
            obj["#text"] = "Nothing relevant."

        return {"result": obj}
