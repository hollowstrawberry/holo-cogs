import discord

from arcenciel.views.image_actions import ImageActions


class DeleteModal(discord.ui.Modal):
    def __init__(self, parent_view: ImageActions, parent_interaction: discord.Interaction):
        super().__init__(title="Delete Image")
        self.parent_view = parent_view
        self.parent_interaction = parent_interaction

        self.silent_checkbox = discord.ui.Label(
            text="Silent",
            description="Whether to leave a message about the deletion.",
            component=discord.ui.Checkbox(default=True),
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
        og_user = self.parent_view.og_user
        silent = self.silent_checkbox.component.value
        prompt = self.parent_view.payload["prompt"]
        enclose = lambda s: s[:1997] + "```"
        if interaction.user.id == og_user.id:
            await interaction.response.send_message(
                enclose(f">>> {interaction.user.mention} deleted their requested image with prompt: ```\n{prompt}"),
                allowed_mentions=discord.AllowedMentions.none(),
                ephemeral=silent,
            )
        else:
            await interaction.response.send_message(
                enclose(f">>> {interaction.user.mention} deleted an image requested by {og_user.mention} with ```\n{prompt}"),
                allowed_mentions=discord.AllowedMentions.none(),
                ephemeral=silent,
            )
