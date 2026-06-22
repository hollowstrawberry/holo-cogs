import discord
from discord.ui import View

from gptmemory.constants import VIEW_TIMEOUT, MAX_EMBED_DESCRIPTION


class MemoryListView(View):
    def __init__(self, memories: list[str]):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.memories = memories
        self.message: discord.Message | None = None
        self.button = discord.ui.Button(emoji="🧠", label="Memories...", style=discord.ButtonStyle.gray)
        self.button.callback = self.show_info
        self.add_item(self.button)

    async def show_info(self, interaction: discord.Interaction):
        assert interaction.guild
        embed = discord.Embed()
        user_memories = [memory for memory in self.memories if any(member.name == memory for member in interaction.guild.members)]
        normal_memories = [memory for memory in self.memories if memory not in user_memories]
        embed.description = "🧠 `[Memories:]`\n> " + ", ".join(f"`{mem}`" for mem in normal_memories)
        embed.description += "\n\n👥 `[User memories:]`\n> " + ", ".join(f"`{mem}`" for mem in user_memories)
        embed.description = embed.description[:MAX_EMBED_DESCRIPTION]
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_timeout(self) -> None:
        await super().on_timeout()
        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
