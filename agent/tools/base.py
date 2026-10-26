from abc import ABC, abstractmethod
from dataclasses import asdict

from redbot.core import commands

from agent.base import AgentCogBase
from agent.types import StructuredObject, ToolCall


class ToolBase(ABC):
    display_name: str
    schema: ToolCall
    apis: list[tuple[str, str]] = []  # [(service_name, key),]
    settings: dict[str, str] = {}  # key and default value
    imagegen: bool = False
    required_cog: str | None = None
    headers = {
        "User-Agent": "holo-cogs/v1 (https://github.com/hollowstrawberry/holo-cogs);"
    }

    def __init__(self, ctx: commands.Context, cog: AgentCogBase) -> None:
        self.ctx = ctx
        self.cog = cog
        if not self.display_name or not self.schema:
            raise RuntimeError("Invalid Tool definition")

    def get_setting(self, key: str) -> str:
        all = self.cog.config.tool_settings.value or {}
        return all.get(key) or self.settings.get(key) or ""

    def get_setting_int(self, key: str, min: int | None = None, max: int | None = None) -> int | None:
        all = self.cog.config.tool_settings.value or {}
        sval = all.get(key) or self.settings.get(key) or ""
        try:
            val = int(sval)
        except ValueError:
            return None
        if min is not None and val < min:
            return min
        if max is not None and val > max:
            return max
        return val  

    @classmethod
    def asdict(cls) -> dict[str, object]:
        return asdict(cls.schema)

    @classmethod
    def available_for(cls, ctx: commands.Context, *, auto: bool) -> bool:
        return cls.required_cog is None or ctx.bot.get_cog(cls.required_cog) is not None

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
