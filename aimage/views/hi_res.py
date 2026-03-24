import asyncio
import discord
import discord.ui as ui
from copy import deepcopy

from aimage.constants import ADETAILER_ARGS
from aimage.views.image_actions import ImageActions


class HiresModal(ui.Modal):
    def __init__(self, parent_view: ImageActions, parent_interaction: discord.Interaction, maxsize: int):
        super().__init__(title="Upscale Image")
        assert parent_interaction.guild
        self.parent_view = parent_view
        self.parent_interaction = parent_interaction
        self.parent_button = parent_view.button_upscale
        self.payload = deepcopy(parent_view.payload)
        self.generate_image = parent_view.generate_image

        upscalers = sorted(set(parent_view.cache.get("upscale", [])))
        maxscale = ((maxsize*maxsize) / (self.payload["width"]*self.payload["height"]))**0.5
        scales = [s for s in [1.0, 1.1, 1.25, 1.5, 1.75, 2.0] if s <= maxscale]
        default_scale = 2.0 if 2.0 in scales else scales[-1]

        self.upscaler_select = ui.Label(
            text="Upscaler",
            component=ui.Select(options=[
                discord.SelectOption(label=name, default=i==0)
                for i, name in enumerate(upscalers[:25])
            ])
        )
        self.scale_select = ui.Label(
            text="Scale",
            component=ui.Select(options=[
                discord.SelectOption(label=f"x{num:.2f}", value=str(num), default=num==default_scale)
                for num in scales
            ])
        )
        self.denoising_select = ui.Label(
            text="Denoising",
            description="How much the image will change.",
            component=ui.Select(options=[
                discord.SelectOption(label=f"{num / 100:.2f}", value=str(num / 100), default=num == 40)
                for num in range(0, 100, 5)
            ])
        )
        self.adetailer_select = ui.Label(
            text="ADetailer",
            description="Improves small faces.",
            component=ui.Select(options=[
                discord.SelectOption(label="Enabled", value="1", default=True),
                discord.SelectOption(label="Disabled", value="0"),
            ])
        )

        self.add_item(self.upscaler_select)
        #self.add_item(self.scale_select)
        self.add_item(self.denoising_select)
        self.add_item(self.adetailer_select)


    async def on_submit(self, interaction: discord.Interaction):
        assert self.parent_interaction.message
        assert isinstance(self.upscaler_select.component, discord.ui.Select)
        assert isinstance(self.scale_select.component, discord.ui.Select)
        assert isinstance(self.denoising_select.component, discord.ui.Select)
        assert isinstance(self.adetailer_select.component, discord.ui.Select)

        scale = float(self.scale_select.component.values[0])
        denoise = float(self.denoising_select.component.values[0])
        upscaler = self.upscaler_select.component.values[0]
        adetailer = bool(int(self.adetailer_select.component.values[0]))

        #self.payload["scaleFactor"] = scale
        self.payload["upscaleProfiles"] = [
            {
                "modelName": upscaler,
                "denoise": denoise,
            }
        ]

        params = self.parent_view.metadata.as_dict()
        self.payload["seed"] = int(params["Seed"])
        self.payload["extraSeed"] = int(params.get("Extra Seed", -1))
        self.payload["extraSeedStrength"] = float(params.get("Extra Seed Strength", 0))

        if adetailer:
            self.payload.update(ADETAILER_ARGS)
        elif "adetailer" in self.payload:
            del self.payload["adetailer"]

        await interaction.response.defer(thinking=True)
        message_content = f"Upscale requested by {interaction.user.mention}"
        await self.generate_image(interaction, payload=self.payload, callback=self.edit_callback(), message_content=message_content)
        
        self.parent_button.disabled = True
        await self.parent_interaction.message.edit(view=self.parent_view)


    async def edit_callback(self):
        await asyncio.sleep(1)
        assert self.parent_interaction.message
        self.parent_button.disabled = False
        if not self.parent_view.is_finished():
            try:
                await self.parent_interaction.message.edit(view=self.parent_view)
            except discord.NotFound:
                pass
