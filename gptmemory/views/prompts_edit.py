import discord
from discord.ui import View
from typing import Awaitable, Callable

from gptmemory.constants import VIEW_TIMEOUT, PROMPT_TYPES
from gptmemory.views.prompt_edit_modal import PromptEditodal

class PromptsEditView(View):
    def __init__(self,
                 prompts: dict[str, str],
                 edit_callback: Callable[[str, str], Awaitable],
                 check_owner_callback: Callable[[discord.User | discord.Member], Awaitable[bool]],
                ):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.prompts = prompts
        self.edit_callback = edit_callback
        self.check_owner_callback = check_owner_callback
        self.message: discord.Message | None = None
        self.prompt_buttons: dict[str, discord.ui.Button] = {}
        for name in prompts.keys():
            if name in PROMPT_TYPES or prompts[name].strip():
                self.add_button(name)
        self.create_button = discord.ui.Button(emoji="➕", style=discord.ButtonStyle.green)
        self.create_button.callback = self.create_prompt
        self.add_item(self.create_button)
        self.delete_button = discord.ui.Button(emoji="✖️", style=discord.ButtonStyle.red)
        self.delete_button.callback = self.delete
        self.add_item(self.delete_button)

    def add_button(self, name: str):
        style = discord.ButtonStyle.blurple if name in PROMPT_TYPES else discord.ButtonStyle.gray
        button = discord.ui.Button(emoji="📝", label=name, style=style)
        button.callback = self.prompt_edit_selector(name)
        self.add_item(button)
        self.prompt_buttons[name] = button

    def prompt_edit_selector(self, name: str):
        async def prompt_edit_wrapper(interaction: discord.Interaction):
            await self.edit_prompt(interaction, name, self.prompts[name])
        return prompt_edit_wrapper
    
    def prompt_edit_callback_selector(self, name: str):
        async def prompt_edit_callback_wrapper(prompt: str):
            self.prompts[name] = prompt
            await self.edit_callback(name, prompt)
            if not prompt.strip() and name in self.prompt_buttons and name not in PROMPT_TYPES:
                self.remove_item(self.prompt_buttons[name])
        return prompt_edit_callback_wrapper
    
    async def prompt_create_callback(self, name: str, prompt: str):
        await self.prompt_edit_callback_selector(name)(prompt)
        if self.message:
            self.remove_item(self.delete_button)
            self.remove_item(self.create_button)
            self.add_button(name)
            self.add_item(self.create_button)
            self.add_item(self.delete_button)
            await self.message.edit(view=self)

    async def edit_prompt(self, interaction: discord.Interaction, name: str, prompt: str):
        if not await self.check_owner_callback(interaction.user):
            return await interaction.response.send_message("Only the bot owner can edit prompts.", ephemeral=True)
        modal = PromptEditodal(name, prompt, edit_callback=self.prompt_edit_callback_selector(name))
        await interaction.response.send_modal(modal)

    async def create_prompt(self, interaction: discord.Interaction):
        if not await self.check_owner_callback(interaction.user):
            return await interaction.response.send_message("Only the bot owner can edit prompts.", ephemeral=True)
        modal = PromptEditodal(None, "", create_callback=self.prompt_create_callback)
        await interaction.response.send_modal(modal)

    async def delete(self, interaction: discord.Interaction):
        assert interaction.message
        if not interaction.permissions.manage_messages and not await self.check_owner_callback(interaction.user):
            return await interaction.response.send_message("You don't have permission to delete this.", ephemeral=True)
        await interaction.message.delete()

    async def on_timeout(self) -> None:
        await super().on_timeout()
        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
