import discord
from typing import Awaitable, Callable

from agent.constants import VIEW_TIMEOUT, PERMANENT_PROMPT_TYPES
from agent.views.prompt_edit_modal import PromptEditModal

SELECT_MAX_OPTIONS = 25
PREVIEW_LENGTH = 48
TEXT_BUDGET = 3500


def preview(text: str, limit: int) -> str:
    flat = " ".join(text.split()).replace("`", "'")
    if not flat:
        return "*empty*"
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return f"`{flat}`"


class PromptPanelView(discord.ui.LayoutView):
    def __init__(self,
                 prompts: dict[str, str],
                 edit_callback: Callable[[str, str], Awaitable],
                 check_owner: Callable[[discord.User | discord.Member], Awaitable[bool]],
                 title: str = "Prompt Panel",
                 color: discord.Color = discord.Color(0)
                ):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.prompts = prompts
        self.edit_callback = edit_callback
        self.check_owner = check_owner
        self.title = title
        self.message: discord.Message | None = None
        self.color = color
        self.build()

    # layout

    def groups(self) -> tuple[list[str], list[str]]:
        core: list[str] = list(PERMANENT_PROMPT_TYPES)
        custom = [
            name for name, text in self.prompts.items()
            if name not in PERMANENT_PROMPT_TYPES and text.strip()
        ]
        return core, custom

    def overview(self, core: list[str], custom: list[str], preview_len: int) -> str:
        def line(name: str) -> str:
            label = f"- **{discord.utils.escape_markdown(name)}**"
            if preview_len:
                label += " " + preview(self.prompts.get(name, ""), preview_len)
            return label

        lines = [f"### 📝 {self.title}", "-# CORE", *map(line, core)]
        if custom:
            lines += ["-# CUSTOM", *map(line, custom)]
        return "\n".join(lines)

    def make_select(self, names: list[str], placeholder: str) -> discord.ui.Select:
        select = discord.ui.Select(
            placeholder=placeholder,
            options=[discord.SelectOption(label=n, value=n, emoji="📝") for n in names],
        )

        async def on_select(interaction: discord.Interaction):
            name = select.values[0]
            modal = PromptEditModal(
                name,
                self.prompts.get(name, ""),
                self.check_owner,  # owner check happens in the modal
                edit_callback=self.edit_callback_for(name),
            )
            await interaction.response.send_modal(modal)
            # Re-render so the dropdown resets and the same entry can be picked again.
            await self.refresh(interaction.message)

        select.callback = on_select
        return select

    def build(self):
        self.clear_items()
        core, custom = self.groups()

        text = self.overview(core, custom, PREVIEW_LENGTH)
        if len(text) > TEXT_BUDGET:  # absurdly many prompts: drop previews
            text = self.overview(core, custom, 0)[:3900]

        container = discord.ui.Container(accent_color=self.color)
        container.add_item(discord.ui.TextDisplay(text))
        container.add_item(discord.ui.Separator())

        for label, names in (("core", core), ("custom", custom)):
            chunks = [names[i:i + SELECT_MAX_OPTIONS] for i in range(0, len(names), SELECT_MAX_OPTIONS)]
            for n, chunk in enumerate(chunks, 1):
                page = f" ({n}/{len(chunks)})" if len(chunks) > 1 else ""
                container.add_item(discord.ui.ActionRow(
                    self.make_select(chunk, f"Edit a {label} prompt…{page}")
                ))

        create_button = discord.ui.Button(emoji="➕", label="New", style=discord.ButtonStyle.green)
        create_button.callback = self.create_prompt
        close_button = discord.ui.Button(emoji="✖️", label="Close", style=discord.ButtonStyle.red)
        close_button.callback = self.delete
        container.add_item(discord.ui.ActionRow(create_button, close_button))

        self.add_item(container)

    async def refresh(self, fallback: discord.Message | None = None):
        self.build()
        target = self.message or fallback
        if target:
            await target.edit(view=self)


    # callbacks

    async def apply(self, name: str, prompt: str):
        self.prompts[name] = prompt
        await self.edit_callback(name, prompt)
        await self.refresh()

    def edit_callback_for(self, name: str):
        async def callback(prompt: str):
            await self.apply(name, prompt)
        return callback

    async def create_prompt(self, interaction: discord.Interaction):
        if not await self.check_owner(interaction.user):
            return await interaction.response.send_message("Only the bot owner can edit prompts.", ephemeral=True)
        modal = PromptEditModal(None, "", self.check_owner, create_callback=self.apply)
        await interaction.response.send_modal(modal)

    async def delete(self, interaction: discord.Interaction):
        assert interaction.message
        if not interaction.permissions.manage_messages and not await self.check_owner(interaction.user):
            return await interaction.response.send_message("You don't have permission to delete this.", ephemeral=True)
        await interaction.message.delete()

    async def on_timeout(self) -> None:
        await super().on_timeout()
        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
