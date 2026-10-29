import aiohttp
from openai import AsyncOpenAI
from redbot.core import commands, Config
from redbot.core.bot import Red

from agent.types import CompletionResult, AgentContent
from agent.config import AgentCogConfig


class AgentCogBase(commands.Cog):
    """
    Abstract cog to prevent recursive imports
    """
    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.openai_client: AsyncOpenAI | None = None
        self.openrouter_client: AsyncOpenAI | None = None
        self.openwebui_client: AsyncOpenAI | None = None
        self.currently_responding: set[int] = set()
        self.currently_generating: set[int] = set()
        self.config = AgentCogConfig(Config.get_conf(None, identifier=19475820, cog_name="GptMemory"))
        self.config.register_all()
    
    async def execute_captioner(self, ctx: commands.Context, image: AgentContent, result: CompletionResult) -> str:
        raise NotImplementedError()
    
    def is_busy(self, message_id):
        return message_id in self.currently_responding or message_id in self.currently_generating

    def openrouter_price_limits(self, model: str) -> dict[str, object]:
        """Request-body limits in USD per million tokens, keyed by exact model slug."""
        if "$" in model or "/" not in model:
            return {}
        prices = {
            key: field.value[model]
            for key, field in (
                ("prompt", self.config.openrouter_max_input_price),
                ("completion", self.config.openrouter_max_output_price),
            )
            if model in field.value
        }
        return {"provider": {"max_price": prices}} if prices else {}
