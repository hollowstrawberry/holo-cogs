import discord
from discord.ui import View

from gptmemory.constants import BACKTICK_PATTERN, VIEW_TIMEOUT, MAX_EMBED_DESCRIPTION


class MemoryInfoView(View):
    def __init__(self, name: str, content: str):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.name = name
        self.content = BACKTICK_PATTERN.sub('`', content)
        self.message: discord.Message | None = None
        self.button = discord.ui.Button(emoji="💭", label=name, style=discord.ButtonStyle.gray)
        self.button.callback = self.show_info
        self.add_item(self.button)

    async def show_info(self, interaction: discord.Interaction):
        embed = discord.Embed()
        embed.description = f"🧠 `{self.name}` ```\n{self.content}"[:MAX_EMBED_DESCRIPTION - 4] + "\n```"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_timeout(self) -> None:
        await super().on_timeout()
        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
