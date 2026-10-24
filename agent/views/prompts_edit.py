from hashlib import sha256

import discord
from agent.constants import PERMANENT_PROMPT_TYPES
from agent.views.prompt_edit_modal import PromptEditModal
from agent.views.prompt_show import PromptView
from agent.views.prompt_store import (
    OwnerCheck,
    PromptButton,
    PromptKind,
    PromptMessageView,
    PromptRef,
    PromptStore,
    display_name,
)

PAGE_SIZE = 25


class PromptSelect(discord.ui.Select["PromptsEditView"]):
    def __init__(self, kind: PromptKind, values: dict[str, str], names: list[str]):
        self.refs = {sha256(name.encode()).hexdigest(): PromptRef(kind, name) for name in names}
        options = [
            discord.SelectOption(
                label=ref.name[:100],
                value=key,
                description=f"{len(values[ref.name]):,} characters" if values[ref.name].strip() else "Empty",
            )
            for key, ref in self.refs.items()
        ]
        super().__init__(
            placeholder="Select a module…" if kind == "module" else "Select a prompt key…",
            options=options or [discord.SelectOption(label="No prompt keys yet", value="empty")],
            disabled=not options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        ref = self.refs.get(self.values[0])
        if ref is None or self.view.store.get(ref) is None:
            await interaction.response.send_message("This prompt no longer exists. Reopen the panel.", ephemeral=True)
            return
        await PromptView.send_private(interaction, self.view.store, ref, self.view.check_owner, self.view.color)


class PromptsEditView(PromptMessageView):
    def __init__(self, store: PromptStore, check_owner: OwnerCheck, title: str, color: discord.Color):
        super().__init__()
        self.store, self.check_owner = store, check_owner
        self.title, self.color = title, color
        self.page = 0
        self.render()

    def render(self) -> None:
        self.clear_items()
        modules, keys = self.store.values("module"), self.store.values("key")
        names = sorted(keys, key=lambda name: (name.casefold(), name))
        pages = max(1, (len(names) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(self.page, pages - 1)
        self.module_select = PromptSelect(
            "module", modules, [name for name in PERMANENT_PROMPT_TYPES if name in modules]
        )
        self.key_select = PromptSelect("key", keys, names[self.page * PAGE_SIZE : (self.page + 1) * PAGE_SIZE])
        container: discord.ui.Container[PromptsEditView] = discord.ui.Container(accent_color=self.color)
        container.add_item(
            discord.ui.TextDisplay(
                f"## {display_name(self.title)}\n{len(modules)} modules · {len(keys)} custom keys "
                f"({sum(not value.strip() for value in keys.values())} empty)"
            )
        )
        container.add_item(discord.ui.TextDisplay("**Modules**"))
        container.add_item(discord.ui.ActionRow(self.module_select))
        container.add_item(discord.ui.TextDisplay(f"**Custom prompt keys** · Page {self.page + 1}/{pages}"))
        container.add_item(discord.ui.ActionRow(self.key_select))
        if pages > 1:
            container.add_item(
                discord.ui.ActionRow(
                    PromptButton("Previous", self.previous, disabled=self.page == 0),
                    PromptButton("Next", self.next, disabled=self.page == pages - 1),
                )
            )
        container.add_item(
            discord.ui.ActionRow(
                PromptButton("New key", self.create_prompt, style=discord.ButtonStyle.success),
                PromptButton("Refresh", self.reload),
                PromptButton("Close panel", self.close),
            )
        )
        self.add_item(container)

    async def previous(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        await self.reload(interaction)

    async def next(self, interaction: discord.Interaction) -> None:
        self.page += 1
        await self.reload(interaction)

    async def reload(self, interaction: discord.Interaction) -> None:
        self.render()
        await interaction.response.edit_message(view=self, allowed_mentions=discord.AllowedMentions.none())

    async def create_prompt(self, interaction: discord.Interaction) -> None:
        if not await self.check_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner can create prompt keys.", ephemeral=True)
            return
        await interaction.response.send_modal(
            PromptEditModal(
                self.store,
                None,
                self.check_owner,
                lambda: not self.is_finished(),
                self.refresh,
            )
        )

    async def refresh(self) -> None:
        if self.message and not self.is_finished():
            self.render()
            try:
                await self.message.edit(view=self, allowed_mentions=discord.AllowedMentions.none())
            except (discord.NotFound, discord.Forbidden):
                self.stop()

    async def close(self, interaction: discord.Interaction) -> None:
        if not interaction.permissions.manage_messages and not await self.check_owner(interaction.user):
            await interaction.response.send_message("You don't have permission to close this panel.", ephemeral=True)
            return
        await interaction.response.defer()
        if interaction.message:
            await interaction.message.delete()
        self.stop()
