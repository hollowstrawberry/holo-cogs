import aiohttp
from openai import AsyncOpenAI
from redbot.core import commands, Config
from redbot.core.bot import Red

from agent.schema import CompletionResult, AgentImageContent
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
        
    async def find_last_sd_generated_image_resolution(self, ctx: commands.Context) -> tuple[int | None, int | None]:
        raise NotImplementedError()
    
    async def execute_captioner(self, ctx: commands.Context, image: AgentImageContent, result: CompletionResult) -> str:
        raise NotImplementedError()
    
    def is_busy(self, message_id):
        return message_id in self.currently_responding or message_id in self.currently_generating
