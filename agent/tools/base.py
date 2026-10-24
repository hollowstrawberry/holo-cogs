import json
from abc import ABC, abstractmethod
from dataclasses import asdict

from redbot.core import commands

from agent.base import AgentCogBase
from agent.schema import StructuredObject, ToolCall


class ToolBase(ABC):
    display_name: str
    schema: ToolCall
    apis: list[tuple[str, str]] = []  # [(service_name, key),]
    settings: dict[str, str] = {}  # key and default value
    imagegen: bool = False
    direct_only: bool = False
    required_cog: str | None = None

    def __init__(self, ctx: commands.Context, cog: AgentCogBase) -> None:
        self.ctx = ctx
        self.cog = cog
        if not self.display_name or not self.schema:
            raise RuntimeError("Invalid Tool definition")

    def get_setting(self, key: str) -> str:
        all = self.cog.config.tool_settings.value or {}
        return all.get(key) or self.settings.get(key) or ""

    @classmethod
    def asdict(cls) -> dict[str, object]:
        return asdict(cls.schema)

    @classmethod
    def available_for(cls, ctx: commands.Context, *, auto: bool) -> bool:
        return (not auto or not cls.direct_only) and (
            cls.required_cog is None or ctx.bot.get_cog(cls.required_cog) is not None
        )

    async def run_cached(
        self, arguments: dict[str, object], results: dict[tuple[str, str], str]
    ) -> StructuredObject | str:
        """Avoid repeating a write with identical arguments during one response."""
        if not self.direct_only:
            return await self.run(arguments)
        key = (self.schema.function.name, json.dumps(arguments, sort_keys=True))
        if key in results:
            return results[key]
        result = await self.run(arguments)
        if isinstance(result, str):
            results[key] = result
        return result

    @abstractmethod
    async def run(self, arguments: dict[str, object]) -> StructuredObject | str:
        raise NotImplementedError


def get_all_tools() -> list[type[ToolBase]]:
    tool_types: set[type[ToolBase]] = set()
    for tool in ToolBase.__subclasses__():
        if subs := tool.__subclasses__():
            tool_types.update(subs)
        else:
            tool_types.update((tool,))
    return sorted(tool_types, key=lambda t: t.display_name)
