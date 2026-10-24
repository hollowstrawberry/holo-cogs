import io
import json
import discord

from arcenciel.constants import VIEW_TIMEOUT
from arcenciel.moss_metadata.view import LayoutSource, send_layout


class ImageInfoView(discord.ui.View):
    def __init__(self, raw_metadata: str, layout_source: LayoutSource | None = None):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.raw_metadata = raw_metadata
        self.layout_source = layout_source
        if layout_source is not None:
            button = discord.ui.Button(label="View layout", emoji="📐")
            button.callback = self.view_layout
            self.add_item(button)
        self.button = discord.ui.Button(emoji='🔧', label='View Full', style=discord.ButtonStyle.blurple)
        self.button.callback = self.view_full_parameters
        self.add_item(self.button)

    async def view_layout(self, interaction: discord.Interaction) -> None:
        if self.layout_source is not None:
            await send_layout(interaction, self.layout_source, VIEW_TIMEOUT)

    async def view_full_parameters(self, interaction: discord.Interaction):
        try:
            j = json.loads(self.raw_metadata)
            content = json.dumps(j, indent=2)  # prettify
        except json.JSONDecodeError:
            content = self.raw_metadata
        with io.StringIO() as f:
            f.write(content)
            f.seek(0)
            file = discord.File(f, f"parameters_{interaction.id}.json")  # type: ignore
            await interaction.response.send_message(file=file, ephemeral=True)
