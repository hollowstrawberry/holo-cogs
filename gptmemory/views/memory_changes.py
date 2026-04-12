import discord
from discord.ui import View

from gptmemory.schema import MemoryChangeResult
from gptmemory.constants import VIEW_TIMEOUT, EMPTY, MAX_EMBED_DESCRIPTION, MAX_EMBED_FIELD, MAX_EMBED_NAME


class MemoryChangeView(View):
    def __init__(self, memory_changes: list[MemoryChangeResult]):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.memory_changes = memory_changes
        self.message: discord.Message | None = None
        self.button = discord.ui.Button(emoji="💭", label="...", style=discord.ButtonStyle.gray)
        self.button.callback = self.show_memory_changes
        self.add_item(self.button)

    async def show_memory_changes(self, interaction: discord.Interaction):
        embed = discord.Embed()
        
        for change in self.memory_changes[:24]:
            before = f"```\n{EMPTY}" if not change.before else f"```\n{change.before}"
            after = f"```\n{EMPTY}" if not change.after else f"```\n{change.after}"
            if len(self.memory_changes) == 1:
                embed.description = f"🚮 `{change.name}` {before}"[:MAX_EMBED_DESCRIPTION // 2 - 3] + "```"
                embed.description += f"\n🆕 `{change.name}` {after}"[:MAX_EMBED_DESCRIPTION // 2 - 3] + "```"
            else:
                embed.add_field(name=f"🚮 {change.name}"[:MAX_EMBED_NAME], value=f"{before[:MAX_EMBED_FIELD - 7]}```", inline=True)
                embed.add_field(name=f"🆕 {change.name}"[:MAX_EMBED_NAME], value=f"{after[:MAX_EMBED_FIELD - 7]}```", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_timeout(self) -> None:
        await super().on_timeout()
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.NotFound:
                pass
