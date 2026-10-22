import discord
import tiktoken
from typing import Awaitable, Callable

from agent import constants


class MemoryEditModal(discord.ui.Modal):
    def __init__(self,
                 name: str,
                 content: str,
                 edit_callback: Callable[[str], Awaitable],
                ):
        super().__init__(title="Edit Memory")
        self.name = name
        self.content = content
        self.edit_callback = edit_callback
        self.content_edit = discord.ui.Label(
            text=name,
            component=discord.ui.TextInput(
                style=discord.TextStyle.long,
                default=content,
                required=False,
            )
        )
        self.add_item(self.content_edit)
        
    async def on_submit(self, interaction: discord.Interaction):
        assert isinstance(interaction.user, discord.Member)
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("You can view the memory, but only an admin can edit it.", ephemeral=True)
        assert isinstance(self.content_edit.component, discord.ui.TextInput)
        content = self.content_edit.component.value
        await self.edit_callback(content)
        tokens = tiktoken.get_encoding(constants.TOKEN_ENCODING).encode(content)
        embed = discord.Embed()
        embed.description = f"`{self.name}` has been edited."
        embed.add_field(name="Length", value=len(content))
        embed.add_field(name="Tokens", value=len(tokens))
        await interaction.response.send_message(embed=embed, ephemeral=True)
