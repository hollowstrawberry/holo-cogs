import discord
import discord.ui as ui
from copy import deepcopy

from aimage.views.image_actions import ImageActions


class DeleteModal(ui.Modal):
    def __init__(self, parent_view: ImageActions, parent_interaction: discord.Interaction):
        super().__init__(title="Delete Image")
        self.parent_view = parent_view
        self.parent_interaction = parent_interaction

        self.silent_checkbox = ui.Label(
            text="Silent",
            description="Whether to leave a message about the deletion.",
            component=ui.Checkbox(default=True),
        )
        self.add_item(self.silent_checkbox)


    async def on_submit(self, interaction: discord.Interaction):
        assert self.parent_interaction.message and isinstance(self.silent_checkbox.component, discord.ui.Checkbox)
        try:
            await self.parent_interaction.message.delete()
        except discord.NotFound:
            return await interaction.response.send_message(
                "The image already got deleted.",
                ephemeral=True,
            )

        self.parent_view.stop()
        silent = self.silent_checkbox.component.value
        prompt = self.parent_view.payload["prompt"]
        if interaction.user.id == self.parent_view.og_user.id:
            await interaction.response.send_message(
                f">>> {interaction.user.mention} deleted their requested image with prompt: ```\n{prompt}"[:1997] + "```",
                allowed_mentions=discord.AllowedMentions.none(),
                ephemeral=silent,
            )
        else:
            await interaction.response.send_message(
                f">>> {interaction.user.mention} deleted an image requested by {self.parent_view.og_user.mention} with ```\n{prompt}"[:1997] + "```",
                allowed_mentions=discord.AllowedMentions.none(),
                ephemeral=silent,
            )        
