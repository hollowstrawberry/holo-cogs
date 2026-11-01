import discord
import tiktoken
from typing import Awaitable, Callable

from agent import constants
from agent.base import AgentCogBase


class PromptEditModal(discord.ui.Modal):
    def __init__(
        self,
        cog: AgentCogBase,
        name: str | None,
        prompt: str,
        edit_callback: Callable[[str], Awaitable] | None = None,
        create_callback: Callable[[str, str], Awaitable] | None = None,
    ):
        super().__init__(title="Prompt Editing")
        self.cog = cog
        self.name = name
        self.prompt = prompt
        self.edit_callback = edit_callback
        self.create_callback = create_callback
        self.prompt_edit = discord.ui.Label(
            text=name or "Prompt Key Value",
            component=discord.ui.TextInput(
                style=discord.TextStyle.long,
                default=prompt,
                required=False,
            ),
        )
        self.prompt_name_edit = discord.ui.Label(
            text="Prompt Key Name",
            component=discord.ui.TextInput(
                style=discord.TextStyle.short,
                min_length=3,
            ),
        )
        if not name:
            self.add_item(self.prompt_name_edit)
        self.add_item(self.prompt_edit)

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.cog.bot.is_owner(interaction.user):
            return await interaction.response.send_message(
                "You can view the prompt, but only the bot owner can edit it.", ephemeral=True
            )
        assert isinstance(self.prompt_edit.component, discord.ui.TextInput)
        assert isinstance(self.prompt_name_edit.component, discord.ui.TextInput)
        prompt = self.prompt_edit.component.value
        if self.name and self.edit_callback:
            await self.edit_callback(prompt)
        elif self.create_callback:
            name = self.prompt_name_edit.component.value.replace("`", "").strip()
            if not name:
                return await interaction.response.send_message("Invalid key name.", ephemeral=True)
            else:
                await self.create_callback(name, prompt)
        tokens = tiktoken.get_encoding(constants.TOKEN_ENCODING).encode(prompt)
        embed = discord.Embed()
        embed.description = f"{self.name} has been edited."
        embed.add_field(name="Length", value=len(prompt))
        embed.add_field(name="Tokens", value=len(tokens))
        await interaction.response.send_message(embed=embed, ephemeral=True)
