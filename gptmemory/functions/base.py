from abc import ABC, abstractmethod
from dataclasses import asdict
from discord.ext import commands

from gptmemory.schema import ToolCall
from gptmemory.base import GptMemoryBase


class FunctionCallBase(ABC):
    schema: ToolCall
    apis: list[tuple[str, str]] = []  # [(service_name, key),]
    settings: list[str] = []

    def __init__(self, ctx: commands.Context, cog: GptMemoryBase):
        self.ctx = ctx
        self.cog = cog

    @classmethod
    def asdict(cls):
        return asdict(cls.schema)

    @abstractmethod
    async def run(self, arguments: dict) -> str:
        raise NotImplementedError
    

def get_all_function_calls() -> list[type[FunctionCallBase]]:
    return FunctionCallBase.__subclasses__()
