import json
import logging
import aiohttp
from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase

log = logging.getLogger("red.holo-cogs.gptmemory")


class SearchFunctionCall(FunctionCallBase):
    apis = [("serper", "api_key")]
    schema = ToolCall(
        Function(
            name="search_google",
            description="Googles a query for any unknown information or for updates on old information.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    }},
                required=["query"],
            )))

    async def run(self, arguments: dict) -> str:
        api_key = (await self.ctx.bot.get_shared_api_tokens("serper")).get("api_key")
        if not api_key:
            log.error("Tried to do a google search but serper api_key not found")
            return "An error occured while searching Google."

        url = "https://google.serper.dev/search"
        query = arguments["query"]
        log.info(f"{query=}")
        payload = json.dumps({"q": query})
        headers = {'X-API-KEY': api_key, 'Content-Type': 'application/json'}
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.post(url, data=payload) as response:
                    response.raise_for_status()
                    data = await response.json()
        except aiohttp.ClientError:
            log.exception("Failed request to serper.io")
            return "An error occured while searching Google."

        content = "[Google Search result] "

        if answer_box := data.get("answerBox", {}):
            if "title" in answer_box and "answer" in answer_box:
                content += f"[Title: {answer_box['title']}] [Answer: {answer_box['answer']}] "
            if "source" in answer_box:
                content += f"[Source: {answer_box['source']}] "
            if "snippet" in answer_box:
                content += f"[snippet:] {answer_box['snippet']} "

        if graph := data.get("knowledgeGraph", {}):
            if "title" in graph:
                content += f"[Title: {graph['title']}] "
            if "type" in graph:
                content += f"[Type: {graph['type']}] "
            if "description" in graph:
                content += f"[Description: {graph['description']}] "
            if "website" in graph:
                content += f"[Website: {graph['website']}] "
            for attribute, value in graph.get("attributes", {}).items():
                content += f"[{attribute}: {value}] "

        if organic_results := data.get("organic", []):
            content += f"[First result URL: {organic_results[0]['link']}] [First result snippet:] {organic_results[0]['snippet']}"

        if len(content) < 25:
            content += "Nothing relevant."

        return content
