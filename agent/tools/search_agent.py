import logging
import asyncio
from openai import NotGiven

from agent.types import ToolCall, Function, Parameters
from agent.constants import COMPLETION_STREAM_FLAG
from agent.tools.base import ToolBase

log = logging.getLogger("agent.searchweb")


class AgenticSearchTool(ToolBase):
    display_name = "agent_search"
    settings = {
        "search_emoji": "🌐",
        "search_timeout": "10",
    }
    schema = ToolCall(
        Function(
            name="web_search",
            description="Search for textual information beyond your base knowledge, such as: recent events, new technologies, real-time data, niche documentation.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string",
                    },
                },
                required=["query"],
            )))

    async def run(self, arguments: dict) -> str:
        assert self.ctx.guild and self.cog.openai_client and self.cog.openrouter_client
        if self.ctx.bot_permissions.add_reactions:
            emoji = self.get_setting("search_emoji")
            asyncio.create_task(self.ctx.message.add_reaction(emoji))

        model = self.cog.config[self.ctx.guild].model_responder.value
        timeout = self.get_setting_int("search_timeout", 1, 1000) or 10
        try:
            if "$" in model:  # openwebui
                log.error("Tried to use agent_search with a openwebui model, which is not possible.")
                return "<error>Web search is not possible at this time</error>"
            elif "/" in model:  # openrouter
                response = await self.cog.openrouter_client.chat.completions.create(
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
                    stream=COMPLETION_STREAM_FLAG,
                    extra_body={
                        **self.cog.openrouter_price_limits(model),
                        "plugins": [
                            {
                                "id": "web",
                                "max_results": 2,
                            }
                        ],
                    },
                    web_search_options={"search_context_size": "low"},
                    timeout=timeout,
                )
                assert response.usage
                output_text = response.choices[0].message.content or ""
                input_tokens = response.usage.prompt_tokens
                output_tokens = response.usage.completion_tokens
            else:
                response = await self.cog.openai_client.responses.create(
                    model=model,
                    reasoning=NotGiven() if "gpt-4" in model else {"effort": "low"},  # type: ignore
                    tools=[{"type": "web_search"}],  # type: ignore
                    input=arguments["query"],
                    timeout=timeout,
                )
                assert response.usage
                output_text = response.output_text
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
        except TimeoutError:
            log.error("Agent search timed out")
            return "<error>Search timed out.</error>"

        log.info(f"WebSearchResult(input_tokens={input_tokens}, output_tokens={output_tokens})")
        return output_text
