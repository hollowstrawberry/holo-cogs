import re
import discord
import discord.ui as ui
from copy import deepcopy

from aimage.constants import NEWLINE_SEPARATOR_PATTERN, PIPE_SEPARATOR_PATTERN
from aimage.views.image_actions import ImageActions


class ModifyModal(ui.Modal):
    def __init__(self, parent_view: ImageActions):
        super().__init__(title="Image Generation")
        self.parent_view = parent_view
        self.parent_button = parent_view.button_modify
        self.payload = deepcopy(parent_view.payload)
        self.params = self.parent_view.metadata.as_dict()
        self.generate_image = parent_view.generate_image
        
        self.prompt_edit = ui.Label(
            text="Prompt",
            component=ui.TextInput(
                style=discord.TextStyle.long,
                default=self.params.get("Prompt") or self.payload["prompt"],
                min_length=4
            )
        )
        self.negative_prompt_edit = ui.Label(
            text="Negative Prompt",
            component=ui.TextInput(
                style=discord.TextStyle.long,
                default=self.params.get("Negative Prompt") or self.payload["negativePrompt"],
                min_length=0
            )
        )
        self.seed_select = ui.Label(
            text="Seed",
            description="You can make a new image or modify the current image.",
            component=ui.Select(options=[
                discord.SelectOption(label="Keep image", value="0", default=True),
                discord.SelectOption(label="Reroll image", value="1"),
            ])
        )

        self.add_item(self.seed_select)
        self.add_item(self.prompt_edit)
        self.add_item(self.negative_prompt_edit)
        

    async def on_submit(self, interaction: discord.Interaction):
        assert isinstance(self.prompt_edit.component, discord.ui.TextInput)
        assert isinstance(self.negative_prompt_edit.component, discord.ui.TextInput)
        assert isinstance(self.seed_select.component, discord.ui.Select)
        
        prompt = self.prompt_edit.component.value
        negative_prompt = self.negative_prompt_edit.component.value
        is_prompt_unchanged = prompt == self.params["Prompt"] and negative_prompt == self.params["Negative Prompt"]
        reroll = bool(int(self.seed_select.component.values[0]))
        
        if not is_prompt_unchanged and self.payload.get("attentionCouple"):  # parse regional prompt
            prompt = PIPE_SEPARATOR_PATTERN.sub("\n", prompt)
            prompt = NEWLINE_SEPARATOR_PATTERN.sub("\n", prompt)
            regions = self.payload["attentionCouple"]["regions"]
            region_prompts = [p.strip() for p in prompt.split("\n")]
            if len(region_prompts) != len(regions):
                content = ":warning: This image has regional prompts, but your edited prompt didn't result in the same number of regions."
                return await interaction.response.send_message(content=content, ephemeral=True)
            for i, region_prompt in enumerate(region_prompts):
                regions[i]["prompt"] = region_prompt
        
        if not is_prompt_unchanged:
            self.payload["prompt"] = prompt
            self.payload["negativePrompt"] = negative_prompt

        if "loras" in self.payload:
            del self.payload["loras"]  # already gets parsed from prompt by generate_image

        if reroll:
            self.payload["seed"] = -1
            self.payload["extraSeed"] = -1
            self.payload["extraSeedStrength"] = 0
        else:
            self.payload["seed"] = int(self.params.get("Seed", -1))
            self.payload["extraSeed"] = int(self.params.get("Extra Seed", -1))
            self.payload["extraSeedStrength"] = float(self.params.get("Extra Seed Strength", 0.0))

        await interaction.response.defer(thinking=True)
        message_content = f"Reroll requested by {interaction.user.mention}" if is_prompt_unchanged else f"Change requested by {interaction.user.mention}"
        await self.generate_image(interaction, payload=self.payload, message_content=message_content)
