import logging
import asyncio
from tavily import AsyncTavilyClient

from gptmemory.utils import add_xml_group
from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.tools.base import ToolBase

log = logging.getLogger("gptmemory.searchweb")


class TavilySearchTool(ToolBase):
    display_name = "tavily_search"
    apis = [("tavily", "api_key")]
    settings = {"search_emoji": "🌐"}
    schema = ToolCall(
        Function(
            name="web_search",
            description="Search for textual information beyond your base knowledge, such as: recent events, new technologies, real-time data, niche documentation. Uses tavily api.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string"
                    },
                },
                required=["query"],
            )))

    async def run(self, arguments: dict) -> dict | str:
        api_key = (await self.ctx.bot.get_shared_api_tokens("tavily")).get("api_key")
        if not api_key:
            log.error("Tried to do a web search but tavily api_key not found. Consider using one of the other search tools.")
            return "<error>An error occured while searching the web.</error>"
        
        if self.ctx.bot_permissions.add_reactions:
            emoji = await self.get_setting("search_emoji")
            asyncio.create_task(self.ctx.message.add_reaction(emoji))

        client = AsyncTavilyClient(api_key)
        result = await client.search(
            query = arguments["query"],
            search_depth="basic",
            include_answer="advanced",
            max_results=5,
            timeout=15,
        )
        obj = {
            "query": result["query"],
            "answer": result["answer"],
        }
        result_items = []
        for item in result["results"]:
            result_items.append({
                "@url": item["url"],
                "title": item["title"],
                "content": item["content"],
            })
        add_xml_group(obj, result_items, "results")
        return {"search": obj}
