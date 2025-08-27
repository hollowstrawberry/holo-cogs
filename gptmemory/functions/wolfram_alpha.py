import logging
import aiohttp
import xml.etree.ElementTree as ElementTree

from gptmemory.constants import FARENHEIT_PATTERN
from gptmemory.utils import farenheit_to_celsius
from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase

log = logging.getLogger("gptmemory.wolframalpha")


class WolframAlphaFunctionCall(FunctionCallBase):
    apis = [("wolframalpha", "appid")]
    schema = ToolCall(
        Function(
            name="ask_wolframalpha",
            description="Asks Wolfram Alpha about math, exchange rates, or the weather. Do not use for price checks or other searches.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string",
                        "description": "A math operation, currency conversion, or weather question."
                    }},
                required=["query"],
            )))

    async def run(self, arguments: dict) -> str:
        api_key = (await self.ctx.bot.get_shared_api_tokens("wolframalpha")).get("appid")
        if not api_key:
            log.error("No appid set for wolframalpha")
            return "An error occured while asking Wolfram Alpha."

        url = "http://api.wolframalpha.com/v2/query?"
        query = arguments["query"]
        payload = {"input": query, "appid": api_key}
        headers = {"user-agent": "Red-cog/2.0.0"}

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, params=payload) as response:
                    response.raise_for_status()
                    result = await response.text()
        except aiohttp.ClientError:
            log.exception("Asking Wolfram Alpha")
            return "An error occured while asking Wolfram Alpha."

        root = ElementTree.fromstring(result)
        plaintext = []
        for pt in root.findall(".//plaintext"):
            if pt.text:
                plaintext.append(pt.text.capitalize())
        if not plaintext:
            return f"Wolfram Alpha is unable to answer the question. Try to answer with your own knowledge."
        content = "\n".join(plaintext[:3])  # lines after the 3rd are often irrelevant in answers such as currency conversion

        if FARENHEIT_PATTERN.search(content):
            content = FARENHEIT_PATTERN.sub(farenheit_to_celsius, content)

        return f"[Wolfram Alpha] [Question: {query}] [Answer:] {content}"
