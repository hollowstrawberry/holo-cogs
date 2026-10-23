from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass

import aiohttp
import discord
from arcenciel.base import ArcencielBase
from arcenciel.constants import VIEW_TIMEOUT
from arcenciel.moss import is_array, is_object
from arcenciel.utils import ImageGenError, clean_model


@dataclass(frozen=True)
class Checkpoint:
    name: str
    label: str
    base: str
    title: str

    @property
    def key(self) -> str:
        # Select values are limited to 100 characters, unlike model filenames.
        return hashlib.sha256(self.name.encode()).hexdigest()


def checkpoints_from_catalog(catalog: Mapping[str, object]) -> list[Checkpoint]:
    entries = catalog.get("checkpoints")
    if not is_array(entries):
        raise ValueError("Invalid generator checkpoint catalog.")
    found: dict[str, Checkpoint] = {}
    for entry in entries:
        if not is_object(entry):
            raise ValueError("Invalid generator checkpoint entry.")
        name = entry.get("name")
        if entry.get("storageState") != "loaded" or not isinstance(name, str) or not name:
            continue
        base, title = entry.get("baseModel"), entry.get("modelTitle")
        found[name] = Checkpoint(
            name,
            clean_model(name).removesuffix(".safetensors"),
            base if isinstance(base, str) and base else "Unknown",
            title if isinstance(title, str) else "",
        )
    return sorted(found.values(), key=lambda item: (item.label.casefold(), item.name.casefold()))


def exact_checkpoint(entries: list[Checkpoint], query: str) -> Checkpoint | None:
    query = query.strip().strip("`").casefold().removesuffix(".safetensors")
    raw = [item for item in entries if item.name.casefold().removesuffix(".safetensors") == query]
    matches = raw or [item for item in entries if item.label.casefold() == query]
    return matches[0] if len(matches) == 1 else None


class _PickerSelect(discord.ui.Select["CheckpointSelectView"]):
    def __init__(self, row: int, handler: Callable[[discord.Interaction], Awaitable[None]]):
        super().__init__(row=row)
        self.handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.handler(interaction)


class _PickerButton(discord.ui.Button["CheckpointSelectView"]):
    def __init__(self, label: str, handler: Callable[[discord.Interaction], Awaitable[None]]):
        super().__init__(label=label, row=2)
        self.handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.handler(interaction)


class CheckpointSelectView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(
        self,
        cog: ArcencielBase,
        owner: discord.User | discord.Member,
        entries: list[Checkpoint],
        current: str,
        bot_default: str,
        color: discord.Color,
        query: str = "",
    ):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog, self.owner = cog, owner
        self.entries, self.current, self.bot_default, self.color = entries, current, bot_default, color
        self.query, self.base, self.page = query.strip().strip("`"), "", 0
        self.message: discord.Message | None = None
        self.lock = asyncio.Lock()
        self.base_select = _PickerSelect(0, self.filter_base)
        self.checkpoint_select = _PickerSelect(1, self.select_checkpoint)
        self.previous = _PickerButton("◀", self.previous_page)
        self.next = _PickerButton("▶", self.next_page)
        self.search = _PickerButton("Search", self.open_search)
        self.show_all = _PickerButton("All models", self.clear_filters)
        self.reset = _PickerButton("Reset default", self.reset_checkpoint)
        self.rebuild()

    def filtered(self) -> list[Checkpoint]:
        terms = self.query.casefold().split()
        return [
            item
            for item in self.entries
            if (not self.base or item.base == self.base)
            and all(term in f"{item.name} {item.label} {item.base} {item.title}".casefold() for term in terms)
        ]

    def display_name(self, item: Checkpoint) -> str:
        if sum(other.label.casefold() == item.label.casefold() for other in self.entries) > 1:
            return item.name.removesuffix(".safetensors")
        return item.label

    def rebuild(self) -> None:
        self.clear_items()
        bases = sorted({item.base for item in self.entries}, key=str.casefold)
        if self.base not in bases:
            self.base = ""
        # Search also covers bases if a future catalog exceeds Discord's filter limit.
        if 1 < len(bases) <= 24:
            self.base_select.options = [discord.SelectOption(label="All bases", value="all", default=not self.base)] + [
                discord.SelectOption(
                    label=base[:100], value=hashlib.sha256(base.encode()).hexdigest(), default=base == self.base
                )
                for base in bases
            ]
            self.base_select.placeholder = "Filter by model base"
            self.add_item(self.base_select)
        matches = self.filtered()
        self.page = min(self.page, max(0, (len(matches) - 1) // self.PAGE_SIZE))
        start = self.page * self.PAGE_SIZE
        options = [
            discord.SelectOption(
                label=f"{start + index + 1}. {self.display_name(item)}"[:100],
                value=item.key,
                description=item.base[:100],
                default=item.name == self.current,
            )
            for index, item in enumerate(matches[start : start + self.PAGE_SIZE])
        ]
        self.checkpoint_select.options = options or [
            discord.SelectOption(label="No matching checkpoints", value="empty")
        ]
        self.checkpoint_select.disabled = not options
        self.checkpoint_select.placeholder = "Choose your default checkpoint"
        self.add_item(self.checkpoint_select)
        self.previous.disabled = self.page == 0
        self.next.disabled = start + self.PAGE_SIZE >= len(matches)
        self.show_all.disabled = not (self.query or self.base)
        self.reset.disabled = not self.current
        for button in (self.previous, self.next, self.search, self.show_all, self.reset):
            self.add_item(button)

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title="Your default checkpoint", color=self.color)
        if self.current:
            selected = next((item for item in self.entries if item.name == self.current), None)
            value = f"{self.display_name(selected)} · {selected.base}" if selected else f"{self.current} (unavailable)"
        else:
            value = (
                f"No personal override. Commands: {clean_model(self.bot_default) or 'automatic'}. "
                "Agent: its configured default."
            )
        embed.add_field(name="Current selection", value=discord.utils.escape_markdown(value)[:1024], inline=False)
        matches = self.filtered()
        start = self.page * self.PAGE_SIZE
        lines = [
            f"**{start + index + 1}.** {discord.utils.escape_markdown(self.display_name(item))[:90]} · "
            f"{discord.utils.escape_markdown(item.base)[:40]}"
            for index, item in enumerate(matches[start : start + self.PAGE_SIZE])
        ]
        embed.description = "\n".join(lines) or "No checkpoints match these filters. Try Search or All models."
        if self.query:
            embed.add_field(name="Search", value=discord.utils.escape_markdown(self.query)[:1024], inline=False)
        pages = max(1, (len(matches) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        embed.set_footer(
            text=f"{self.owner.display_name} · Page {self.page + 1}/{pages} · "
            f"{len(matches)}/{len(self.entries)} checkpoints"
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("Open your own checkpoint picker with `ckpt`.", ephemeral=True)
            return False
        if self.is_finished():
            await interaction.response.send_message("This picker expired. Run `ckpt` again.", ephemeral=True)
            return False
        return True

    async def update(self, interaction: discord.Interaction) -> None:
        self.rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def filter_base(self, interaction: discord.Interaction) -> None:
        bases = sorted({item.base for item in self.entries}, key=str.casefold)
        selected = self.base_select.values[0]
        self.base = next((base for base in bases if hashlib.sha256(base.encode()).hexdigest() == selected), "")
        self.page = 0
        await self.update(interaction)

    async def previous_page(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        await self.update(interaction)

    async def next_page(self, interaction: discord.Interaction) -> None:
        self.page += 1
        await self.update(interaction)

    async def clear_filters(self, interaction: discord.Interaction) -> None:
        self.query, self.base, self.page = "", "", 0
        await self.update(interaction)

    async def open_search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CheckpointSearchModal(self))

    async def select_checkpoint(self, interaction: discord.Interaction) -> None:
        selected = next((item for item in self.entries if item.key == self.checkpoint_select.values[0]), None)
        await interaction.response.defer()
        async with self.lock:
            try:
                if not self.cog.api:
                    raise ImageGenError("The generator is unavailable.")
                _, catalog = await self.cog.api.refresh_moss_profile()
                self.entries = checkpoints_from_catalog(catalog)
            except (ValueError, ImageGenError, aiohttp.ClientError, TimeoutError):
                await interaction.followup.send(
                    "Could not refresh checkpoints. Your default was not changed; try again.", ephemeral=True
                )
                return
            if self.is_finished():
                await interaction.followup.send("This picker expired. Run `ckpt` again.", ephemeral=True)
                return
            if selected and any(item.name == selected.name for item in self.entries):
                await self.cog.config.user(self.owner).checkpoint.set(selected.name)
                self.current = selected.name
            else:
                await interaction.followup.send(
                    "That checkpoint is no longer available. Please choose another.", ephemeral=True
                )
            self.rebuild()
            await interaction.edit_original_response(embed=self.embed(), view=self)

    async def reset_checkpoint(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        async with self.lock:
            if self.is_finished():
                await interaction.followup.send("This picker expired. Run `ckpt` again.", ephemeral=True)
                return
            await self.cog.config.user(self.owner).checkpoint.set("")
            self.current = ""
            self.bot_default = await self.cog.config.checkpoint() or ""
            self.rebuild()
            await interaction.edit_original_response(embed=self.embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        if self.message:
            with suppress(discord.NotFound, discord.Forbidden):
                await self.message.edit(view=self)


class CheckpointSearchModal(discord.ui.Modal, title="Find a checkpoint"):
    def __init__(self, picker: CheckpointSelectView):
        super().__init__()
        self.picker = picker
        self.query: discord.ui.TextInput[CheckpointSearchModal] = discord.ui.TextInput(
            label="Name or model base",
            default=picker.query[:100],
            required=False,
            max_length=100,
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if await self.picker.interaction_check(interaction):
            self.picker.query, self.picker.page = self.query.value.strip(), 0
            await self.picker.update(interaction)
