import logging
from openai import NotGiven

from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase

log = logging.getLogger("gptmemory.searchweb")


class AgenticSearchFunctionCall(FunctionCallBase):
    schema = ToolCall(
        Function(
            name="search_web",
            description="Make an agent search the internet for up-to-date information.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    }},
                required=["query"],
            )))

    async def run(self, arguments: dict) -> str:
        assert self.ctx.guild and self.cog.openai_client
        model = await self.cog.config.guild(self.ctx.guild).model_responder()
        response = await self.cog.openai_client.responses.create(
            model=model,
            reasoning=NotGiven() if "gpt-4" in model else {"effort": "low"},
            tools=[{"type": "web_search"}],  # type: ignore
            input=arguments["query"]
        )
        assert response.usage and response.output_text
        log.info(f"WebSearchResult(input_tokens={response.usage.input_tokens}, output_tokens={response.usage.output_tokens})")
        return f"[Below is the search result. Please share this information with the user.]\n{response.output_text}"
