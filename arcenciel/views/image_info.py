import io
import json
import discord
from discord.ui import View

from aimage.constants import VIEW_TIMEOUT


class ImageInfoView(View):
    def __init__(self, raw_metadata: str):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.raw_metadata = raw_metadata
        self.button = discord.ui.Button(emoji='🔧', label='View Full', style=discord.ButtonStyle.blurple)
        self.button.callback = self.view_full_parameters
        self.add_item(self.button)

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
