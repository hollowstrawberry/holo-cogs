import discord
import discord.ui as ui
from copy import deepcopy

from aimage.views.image_actions import ImageActions


class VariationModal(ui.Modal):
    def __init__(self, parent_view: ImageActions):
        super().__init__(title="Make image variation")
        self.parent_view = parent_view
        self.parent_button = parent_view.button_variation
        self.payload = deepcopy(parent_view.payload)
        self.params = self.parent_view.metadata.as_dict()
        self.generate_image = parent_view.generate_image

        default_strength_percent = 5
        previous_strength = self.params.get("Extra Seed Strength", 0.0)
        if previous_strength > 0:
            default_strength_percent = round(previous_strength * 100)

        self.subseed_checkbox = ui.Label(
            text="Reroll subseed",
            description="Keeping the subseed while changing the strength may offer finer tuning.",
            component=ui.Checkbox(default=True),
        )
        self.variation_select = ui.Label(
            text="Strength",
            description="How strong the change should be compared to the original image.",
            component=ui.Select(options=[
                discord.SelectOption(label=f"{num}%", value=str(num), default=num==default_strength_percent)
                for num in range(1, 26)
            ]),
        )

        self.add_item(self.variation_select)
        if previous_strength > 0:
            self.add_item(self.subseed_checkbox)


    async def on_submit(self, interaction: discord.Interaction):
        assert isinstance(self.subseed_checkbox.component, discord.ui.Checkbox)
        assert isinstance(self.variation_select.component, discord.ui.Select)

        reroll = self.subseed_checkbox.component.value
        strength = float(self.variation_select.component.values[0]) / 100
        self.payload["seed"] = int(self.params.get("Seed", -1))
        self.payload["extraSeed"] = -1 if reroll else int(self.params.get("Extra Seed", -1))
        self.payload["extraSeedStrength"] = strength

        await interaction.response.defer(thinking=True)
        message_content = f"Variation requested by {interaction.user.mention}"
        await self.generate_image(interaction, payload=self.payload, message_content=message_content)
