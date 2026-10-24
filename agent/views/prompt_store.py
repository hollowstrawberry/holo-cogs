
import discord
import tiktoken
from contextlib import suppress
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Literal

from agent.config_base import ConfigField
from agent.constants import TOKEN_ENCODING, VIEW_TIMEOUT

PromptKind = Literal["module", "key"]
OwnerCheck = Callable[[discord.User | discord.Member], Awaitable[bool]]


@dataclass(frozen=True)
class PromptRef:
    kind: PromptKind
    name: str


@dataclass
class PromptStore:
    modules: dict[str, ConfigField[str]]
    keys: ConfigField[dict[str, str]]

    def values(self, kind: PromptKind) -> dict[str, str]:
        if kind == "module":
            return {name: field.value for name, field in self.modules.items()}
        return dict(self.keys.value)

    def get(self, ref: PromptRef) -> str | None:
        return self.values(ref.kind).get(ref.name)

    async def save(self, ref: PromptRef, value: str, expected: str | None) -> None:
        current = self.get(ref)
        if expected is None and current is not None:
            raise ValueError("That key already exists. Select it to edit it.")
        if current != expected:
            raise ValueError("This prompt changed or was removed. Reopen the editor before saving.")
        # ConfigField updates its cached value before awaiting persistence.
        # Keep the comparison and update together, without an await in between.
        if ref.kind == "module":
            await self.modules[ref.name].set(value)
        else:
            await self.keys.set({**self.keys.value, ref.name: value})


def display_name(name: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(name[:100]))


def prompt_stats(prompt: str) -> str:
    tokens = len(tiktoken.get_encoding(TOKEN_ENCODING).encode(prompt, disallowed_special=()))
    return f"{len(prompt):,} characters · ~{tokens:,} tokens"


class PromptButton(discord.ui.Button[discord.ui.LayoutView]):
    def __init__(
        self,
        label: str,
        action: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ):
        super().__init__(label=label, style=style, disabled=disabled)
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.action(interaction)


class PromptMessageView(discord.ui.LayoutView):
    def __init__(self) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if self.message:
            with suppress(discord.NotFound, discord.Forbidden):
                await self.message.delete()
