import discord
from discord.ui import View

from gptmemory.schema import MemoryChangeResult
from gptmemory.constants import VIEW_TIMEOUT, EMPTY, MAX_EMBED_DESCRIPTION, BACKTICK_PATTERN


class MemoryChangeView(View):
    def __init__(self, memory_changes: list[MemoryChangeResult], standalone: bool):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.standalone = standalone
        self.message: discord.Message | None = None
        for change in memory_changes:
            button = discord.ui.Button(emoji="💭", label=change.name, style=discord.ButtonStyle.gray)
            button.callback = self.memory_change_selector(change)
            self.add_item(button)

    def memory_change_selector(self, change: MemoryChangeResult):
        async def memory_change_wrapper(interaction: discord.Interaction):
            await self.show_memory_change(interaction, change)
        return memory_change_wrapper

    async def show_memory_change(self, interaction: discord.Interaction, change: MemoryChangeResult):
        before = f"```\n{EMPTY}" if not change.before else f"```\n{BACKTICK_PATTERN.sub('`', change.before)}"
        after = f"```\n{EMPTY}" if not change.after else f"```\n{BACKTICK_PATTERN.sub('`', change.after)}"
        embed = discord.Embed()
        embed.description = f"🚮 `{change.name}` {before}"[:MAX_EMBED_DESCRIPTION // 2 - 4] + "\n```"
        embed.description += f"\n🆕 `{change.name}` {after}"[:MAX_EMBED_DESCRIPTION // 2 - 4] + "\n```"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_timeout(self) -> None:
        await super().on_timeout()
        if self.message:
            try:
                if self.standalone:
                    await self.message.delete()
                else:
                    await self.message.edit(view=None)
            except (discord.NotFound, discord.Forbidden):
                pass
