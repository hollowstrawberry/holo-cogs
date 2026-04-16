import discord
from discord.ui import View
from typing import Awaitable, Callable

from gptmemory.constants import BACKTICK_PATTERN, VIEW_TIMEOUT, MAX_EMBED_DESCRIPTION


class PromptView(View):
    def __init__(self,
                 name: str,
                 prompt: str,
                 edit_callback: Callable[[str], Awaitable],
                 check_owner_callback: Callable[[discord.User | discord.Member], Awaitable[bool]],
                ):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.name = name
        self.prompt = prompt
        async def edit_callback_wrapper(prompt: str):
            self.prompt = prompt
            await edit_callback(prompt)
        self.edit_callback = edit_callback_wrapper
        self.check_owner_callback = check_owner_callback
        
        self.message: discord.Message | None = None
        self.show_button = discord.ui.Button(emoji="🔎", style=discord.ButtonStyle.gray)
        self.show_button.callback = self.show_prompt
        self.add_item(self.show_button)
        self.edit_button = discord.ui.Button(emoji="📝", style=discord.ButtonStyle.gray)
        self.edit_button.callback = self.edit_prompt
        self.add_item(self.edit_button)
        self.delete_button = discord.ui.Button(emoji="✖️", style=discord.ButtonStyle.red)
        self.delete_button.callback = self.delete
        self.add_item(self.delete_button)

    async def show_prompt(self, interaction: discord.Interaction):
        embed = discord.Embed()
        embed.description = f"🧠 `{self.name}` ```\n{self.prompt}"[:MAX_EMBED_DESCRIPTION - 4] + "\n```"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def edit_prompt(self, interaction: discord.Interaction):
        if not await self.check_owner_callback(interaction.user):
            return await interaction.response.send_message("Only the bot owner can edit prompts.", ephemeral=True)
        from gptmemory.views.prompt_edit_modal import PromptEditodal
        modal = PromptEditodal(self.name, self.prompt, self.edit_callback)
        await interaction.response.send_modal(modal)

    async def delete(self, interaction: discord.Interaction):
        if not interaction.permissions.manage_messages and not await self.check_owner_callback(interaction.user):
            return await interaction.response.send_message("You don't have permission to delete this.", ephemeral=True)

    async def on_timeout(self) -> None:
        await super().on_timeout()
        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
