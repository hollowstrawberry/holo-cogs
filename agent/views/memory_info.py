import discord
from typing import Awaitable, Callable

from agent.constants import BACKTICK_PATTERN, VIEW_TIMEOUT, MAX_EMBED_DESCRIPTION
from agent.views.memory_edit_modal import MemoryEditModal


class MemoryInfoView(discord.ui.View):
    def __init__(self,
                 name: str,
                 content: str,
                 edit_callback: Callable[[str], Awaitable],
                 check_owner: Callable[[discord.User | discord.Member], Awaitable[bool]]
                ):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.name = name
        self.content = content
        self.edit_callback = edit_callback
        self.check_owner = check_owner
        self.message: discord.Message | None = None
        self.view_button = discord.ui.Button(emoji="💭", label=name, style=discord.ButtonStyle.gray)
        self.view_button.callback = self.show_info
        self.add_item(self.view_button)
        self.edit_button = discord.ui.Button(emoji="📝", style=discord.ButtonStyle.gray)
        self.edit_button.callback = self.edit
        self.add_item(self.edit_button)

    @property
    def clean_content(self):
        return BACKTICK_PATTERN.sub('`', self.content)

    async def show_info(self, interaction: discord.Interaction):
        embed = discord.Embed()
        embed.description = f"🧠 `{self.name}` ```\n{self.content}"[:MAX_EMBED_DESCRIPTION - 4] + "\n```"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def edit(self, interaction: discord.Interaction):
        assert isinstance(interaction.user, discord.Member)
        if not interaction.user.guild_permissions.administrator and not self.check_owner(interaction.user):
            return await interaction.response.send_message("Only an admin can edit memories.", ephemeral=True)
        modal = MemoryEditModal(self.name, self.content, self.edit_callback_wrapper, self.check_owner)
        await interaction.response.send_modal(modal)

    async def edit_callback_wrapper(self, new_content: str):
        self.stop()
        await self.edit_callback(new_content)
        await self.on_timeout()

    async def on_timeout(self) -> None:
        await super().on_timeout()
        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
