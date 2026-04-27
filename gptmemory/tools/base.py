from abc import ABC, abstractmethod
from dataclasses import asdict
from redbot.core import commands

from gptmemory.schema import StructuredObject, ToolCall
from gptmemory.base import GptMemoryBase


class ToolBase(ABC):
    display_name: str
    schema: ToolCall
    apis: list[tuple[str, str]] = []  # [(service_name, key),]
    settings: dict[str, str] = {}  # key and default value

    def __init__(self, ctx: commands.Context, cog: GptMemoryBase):
        self.ctx = ctx
        self.cog = cog
        if not self.display_name or not self.schema:
            raise RuntimeError("Invalid Tool definition")

    async def get_setting(self, key: str) -> str:
        all = await self.cog.config.tool_settings() or {}
        return all.get(key) or self.settings.get(key) or ""

    @classmethod
    def asdict(cls):
        return asdict(cls.schema)

    @abstractmethod
    async def run(self, arguments: dict) -> StructuredObject | str:
        raise NotImplementedError
    

def get_all_tools() -> list[type[ToolBase]]:
    tool_types: set[type[ToolBase]] = set()
    for tool in ToolBase.__subclasses__():
        if subs := tool.__subclasses__():
            tool_types.update(subs)
        else:
            tool_types.add(tool)
    return list(tool_types)
