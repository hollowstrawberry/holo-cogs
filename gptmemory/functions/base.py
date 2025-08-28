from abc import ABC, abstractmethod
from typing import List, Tuple, Type
from dataclasses import asdict
from discord.ext import commands

import gptmemory.commands
from gptmemory.schema import ToolCall


class FunctionCallBase(ABC):
    schema: ToolCall = None # type: ignore
    apis: List[Tuple[str, str]] = []

    def __init__(self, ctx: commands.Context, cog: gptmemory.commands.GptMemoryBase):
        self.ctx = ctx
        self.cog = cog

    @classmethod
    def asdict(cls):
        return asdict(cls.schema)

    @abstractmethod
    async def run(self, arguments: dict) -> str:
        raise NotImplementedError
    

def get_all_function_calls() -> List[Type[FunctionCallBase]]:
    return FunctionCallBase.__subclasses__()
