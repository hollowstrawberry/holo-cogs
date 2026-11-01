import io
import json
import discord

from arcenciel.types import CallbackButton
from arcenciel.constants import VIEW_TIMEOUT
from arcenciel.moss_metadata.view import LayoutSource, send_layout


class ImageInfoView(discord.ui.View):
    def __init__(self, raw_metadata: str, layout_source: LayoutSource | None = None):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.raw_metadata = raw_metadata
        self.layout_source = layout_source
        if layout_source is not None:
            self.add_item(CallbackButton(self.view_layout, "📐", "View layout"))
        self.button = CallbackButton(self.view_full_parameters, '🔧', 'View Full', discord.ButtonStyle.blurple)
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
        with io.BytesIO() as f:
            f.write(content.encode())
            f.seek(0)
            file = discord.File(f, f"parameters_{interaction.id}.json")
            await interaction.response.send_message(file=file, ephemeral=True)
