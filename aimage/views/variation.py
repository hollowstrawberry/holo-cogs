import asyncio
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
        self.generate_image = parent_view.generate_image

        default_strength_percent = 5
        previous_strength = self.payload.get("extraSeedStrength", 0)
        if previous_strength > 0:
            default_strength_percent = round(previous_strength * 100)

        self.subseed_select = ui.Label(
            text="Subseed",
            description="Keeping the subseed while changing the strength may offer finer tuning.",
            component=ui.Select(options=[
                discord.SelectOption(label=f"Reroll subseed", value="1", default=True),
                discord.SelectOption(label=f"Keep subseed", value="0")
            ])
        )
        self.variation_select = ui.Label(
            text="Strength",
            description="How strong the change should be compared to the original image.",
            component=ui.Select(options=[
                discord.SelectOption(label=f"{num}%", value=str(num), default=num==default_strength_percent)
                for num in range(1, 26)
            ])
        )

        if previous_strength > 0:
            self.add_item(self.subseed_select)
        self.add_item(self.variation_select)


    async def on_submit(self, interaction: discord.Interaction):
        assert isinstance(self.subseed_select.component, discord.ui.Select)
        assert isinstance(self.variation_select.component, discord.ui.Select)

        reroll = bool(int(self.subseed_select.component.values[0])) if self.subseed_select.component.values else True
        strength = float(self.variation_select.component.values[0]) / 100
        params = self.parent_view.get_params_dict() or {}
        self.payload["seed"] = int(params.get("seed", -1))
        self.payload["extraSeed"] = -1 if reroll else int(params.get("Extra seed", -1))
        self.payload["extraSeedStrength"] = strength

        await interaction.response.defer(thinking=True)
        message_content = f"Variation requested by {interaction.user.mention}"
        await self.generate_image(interaction, payload=self.payload, message_content=message_content)
