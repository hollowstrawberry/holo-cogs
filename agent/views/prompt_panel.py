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

    def prompt_select(self, items: dict[str, str], placeholder: str) -> discord.ui.Select:
        select = discord.ui.Select(
            placeholder=placeholder,
            options=[
                discord.SelectOption(
                    label=name,
                    value=name,
                    description=f"{len(content)} characters",
                    emoji="📝"
                )
                for name, content in items
            ],
        )

        async def on_select(interaction: discord.Interaction):
            name = select.values[0]
            modal = PromptEditModal(name, self.prompts.get(name, ""), self.check_owner, edit_callback=self.edit_callback_for(name))
            await interaction.response.send_modal(modal)
            await self.refresh(interaction.message)

        select.callback = on_select
        return select

    def build(self):
        self.clear_items()
        module_prompts = {name: content for name, content in self.prompts if name in PERMANENT_PROMPT_TYPES}
        custom_prompts = {name: content for name, content in self.prompts if name not in PERMANENT_PROMPT_TYPES and content}

        container = discord.ui.Container(accent_color=self.color)

        container.add_item(
            discord.ui.TextDisplay(f"## {self.title} Prompt Panel\n{len(module_prompts)} modules, {len(custom_prompts)} custom keys")
        )
        container.add_item(
            discord.ui.TextDisplay("**Modules**")
        )
        container.add_item(
            discord.ui.ActionRow(self.prompt_select(module_prompts, "Open a module prompt..."))
        )
        container.add_item(
            discord.ui.TextDisplay(f"**Custom keys**")
        )
        container.add_item(
            discord.ui.ActionRow(self.prompt_select(custom_prompts, "Open a prompt key..."))
        )
        create_button = discord.ui.Button(emoji="➕", label="New", style=discord.ButtonStyle.green)
        create_button.callback = self.create_prompt
        close_button = discord.ui.Button(emoji="✖️", label="Close", style=discord.ButtonStyle.red)
        close_button.callback = self.delete
        container.add_item(
            discord.ui.ActionRow(create_button, close_button)
        )

        self.add_item(container)

    async def refresh(self, fallback: discord.Message | None = None):
        self.build()
        target = self.message or fallback
        if target:
            await target.edit(view=self)

    async def apply(self, name: str, content: str):
        self.prompts[name] = content
        await self.edit_callback(name, content)
        await self.refresh()

    def edit_callback_for(self, name: str):
        async def callback(content: str):
            await self.apply(name, content)
        return callback

    async def create_prompt(self, interaction: discord.Interaction):
        if not await self.check_owner(interaction.user):
            return await interaction.response.send_message("Only the bot owner can edit prompts.", ephemeral=True)
        modal = PromptEditModal(None, "", self.check_owner, create_callback=self.apply)
        await interaction.response.send_modal(modal)

    async def delete(self, interaction: discord.Interaction):
        assert interaction.message
        if not interaction.permissions.manage_messages and not await self.check_owner(interaction.user):
            return await interaction.response.send_message("You don't have permission to remove this.", ephemeral=True)
        await interaction.message.delete()

    async def on_timeout(self) -> None:
        await super().on_timeout()
        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
