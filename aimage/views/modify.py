import asyncio
import discord
import discord.ui as ui
from copy import deepcopy

from aimage.views.image_actions import ImageActions


class ModifyModal(ui.Modal):
    def __init__(self, parent_view: ImageActions):
        super().__init__(title="Image Generation")
        self.parent_view = parent_view
        self.parent_button = parent_view.button_modify
        self.payload = deepcopy(parent_view.payload)
        self.generate_image = parent_view.generate_image

        self.prompt_edit = ui.Label(
            text="Prompt",
            component=ui.TextInput(
                style=discord.TextStyle.long,
                default=self.payload["prompt"],
                min_length=4
            )
        )
        self.negative_prompt_edit = ui.Label(
            text="Negative Prompt",
            component=ui.TextInput(
                style=discord.TextStyle.long,
                default=self.payload["negativePrompt"],
                min_length=0
            )
        )
        self.seed_select = ui.Label(
            text="Seed",
            description="You can make a new image or modify the current image.",
            component=ui.Select(options=[
                discord.SelectOption(label="Reroll image", value="1", default=True),
                discord.SelectOption(label="Keep image", value="0"),
            ])
        )

        self.add_item(self.seed_select)
        self.add_item(self.prompt_edit)
        self.add_item(self.negative_prompt_edit)
        

    async def on_submit(self, interaction: discord.Interaction):
        assert isinstance(self.prompt_edit.component, discord.ui.TextInput)
        assert isinstance(self.negative_prompt_edit.component, discord.ui.TextInput)
        assert isinstance(self.seed_select.component, discord.ui.Select)
        
        same_prompt = self.prompt_edit.component.value == self.payload["prompt"] and self.negative_prompt_edit.component.value == self.payload["negativePrompt"]
        self.payload["prompt"] = self.prompt_edit.component.value
        self.payload["negativePrompt"] = self.negative_prompt_edit.component.value

        reroll = bool(int(self.seed_select.component.values[0]))
        if reroll:
            self.payload["seed"] = -1
            self.payload["extraSeed"] = -1
            self.payload["extraSeedStrength"] = 0
        else:
            params = self.parent_view.get_params_dict() or {}
            self.payload["seed"] = int(params.get("Seed", -1))
            self.payload["extraSeed"] = int(params.get("Variation seed", -1))
            self.payload["extraSeedStrength"] = float(params.get("Extra seed strength", 0))

        await interaction.response.defer(thinking=True)
        message_content = f"Reroll requested by {interaction.user.mention}" if same_prompt else f"Change requested by {interaction.user.mention}"
        await self.generate_image(interaction, payload=self.payload, message_content=message_content)
