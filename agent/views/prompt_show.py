import discord
from typing import Awaitable, Callable

from agent.base import AgentCogBase
from agent.utils import CallbackButton
from agent.constants import VIEW_TIMEOUT, MAX_EMBED_DESCRIPTION


class PromptView(discord.ui.View):
    def __init__(
        self,
        cog: AgentCogBase,
        name: str,
        prompt: str,
        edit_callback: Callable[[str], Awaitable],
    ):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.name = name
        self.prompt = prompt
        self.edit_callback = edit_callback
        self.message: discord.Message | None = None
        self.show_button = CallbackButton(self.show_prompt, "🔎", style=discord.ButtonStyle.gray)
        self.edit_button = CallbackButton(self.edit_prompt, "📝", style=discord.ButtonStyle.gray)
        self.delete_button = CallbackButton(self.delete, "✖️", style=discord.ButtonStyle.red)
        self.add_item(self.show_button)
        self.add_item(self.edit_button)
        # self.add_item(self.delete_button)

    async def show_prompt(self, interaction: discord.Interaction):
        embed = discord.Embed()
        embed.description = f"🧠 `{self.name}` ```\n{self.prompt}"[: MAX_EMBED_DESCRIPTION - 4] + "\n```"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def edit_prompt(self, interaction: discord.Interaction):
        if not await self.cog.bot.is_owner(interaction.user):
            return await interaction.response.send_message("Only the bot owner can edit prompts.", ephemeral=True)
        from agent.views.prompt_edit_modal import PromptEditModal

        modal = PromptEditModal(self.cog, self.name, self.prompt, self.edit_callback_wrapper)
        await interaction.response.send_modal(modal)

    async def delete(self, interaction: discord.Interaction):
        assert interaction.message
        if not interaction.permissions.manage_messages and not await self.cog.bot.is_owner(interaction.user):
            return await interaction.response.send_message("You don't have permission to delete this.", ephemeral=True)
        await interaction.message.delete()

    async def edit_callback_wrapper(self, prompt: str):
        self.prompt = prompt
        await self.edit_callback(prompt)

    async def on_timeout(self) -> None:
        await super().on_timeout()
        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
