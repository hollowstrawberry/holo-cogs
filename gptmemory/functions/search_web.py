import logging
import asyncio
from openai import NotGiven

from gptmemory.schema import ToolCall, Function, Parameters
from gptmemory.functions.base import FunctionCallBase

log = logging.getLogger("gptmemory.searchweb")


class AgenticSearchFunctionCall(FunctionCallBase):
    schema = ToolCall(
        Function(
            name="search_web",
            description="Search the internet for up-to-date information, such as news, prices, or recent events.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    }},
                required=["query"],
            )))

    async def run(self, arguments: dict) -> str:
        assert self.ctx.guild and self.cog.openai_client and self.cog.openrouter_client
        if self.ctx.bot_permissions.add_reactions:
            _ = asyncio.create_task(self.ctx.message.add_reaction("🌐"))

        model = await self.cog.config.guild(self.ctx.guild).model_responder()
        if "/" in model:  # openrouter
            response = await self.cog.openrouter_client.beta.chat.completions.create(
                model=model,
                reasoning_effort="low",
                messages=[
                    {
                        "role": "system",
                        "content": "Perform a web search based on the user's query and summarize the results. Don't search too deep.",
                    },
                    {
                        "role": "user",
                        "content": arguments["query"],
                    }
                ],
                extra_body={
                    "plugins": [
                        {
                            "id": "web",
                            "max_results": 2,
                        }
                    ],
                },
                web_search_options={"search_context_size": "low"}
            )
            assert response.usage
            output_text = response.choices[0].message.content or ""
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
        else:
            response = await self.cog.openai_client.responses.create(
                model=model,
                reasoning=NotGiven() if "gpt-4" in model else {"effort": "low"},
                tools=[{"type": "web_search"}],  # type: ignore
                input=arguments["query"],
            )
            assert response.usage
            output_text = response.output_text
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens

        log.info(f"WebSearchResult(input_tokens={input_tokens}, output_tokens={output_tokens})")
        return output_text
