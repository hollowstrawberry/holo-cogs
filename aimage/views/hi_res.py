import asyncio
import discord
import discord.ui as ui
from copy import deepcopy

from aimage.constants import ADETAILER_ARGS, DEFAULT_UPSCALER, DEFAULT_DENOISE, DEFAULT_ADETAILER_DENOISE
from aimage.views.image_actions import ImageActions


class HiresModal(ui.Modal):
    def __init__(self, parent_view: ImageActions, parent_interaction: discord.Interaction, maxsize: int):
        super().__init__(title="Upscale Image")
        assert parent_interaction.guild
        assert parent_view.cache["upscale"]
        self.parent_view = parent_view
        self.parent_interaction = parent_interaction
        self.parent_button = parent_view.button_upscale
        self.payload = deepcopy(parent_view.payload)
        self.generate_image = parent_view.generate_image

        upscalers = sorted(set(parent_view.cache["upscale"]))
        default_upscaler = DEFAULT_UPSCALER if DEFAULT_UPSCALER in upscalers else upscalers[-1]
        maxscale = ((maxsize*maxsize) / (self.payload["width"]*self.payload["height"]))**0.5
        scales = [s for s in [1.0, 1.1, 1.25, 1.5, 1.75, 2.0] if s <= maxscale]
        default_scale = scales[-1]
        denoise_steps = list(range(0, 7)) + list(range(8, 21, 2)) + list(range(25, 81, 5))

        self.upscaler_select = ui.Label(
            text="Upscaler",
            component=ui.Select(options=[
                discord.SelectOption(label=name.rsplit(".", 1)[0], value=name, default=name==default_upscaler)
                for name in upscalers[:25]
            ])
        )
        self.scale_select = ui.Label(
            text="Scale",
            component=ui.Select(options=[
                discord.SelectOption(label=f"x{num:.2f}", value=f"{num:.2f}", default=num==default_scale)
                for num in scales
            ])
        )
        self.denoising_select = ui.Label(
            text="Denoise",
            description="How much the image will change.",
            component=ui.Select(options=[
                discord.SelectOption(label=f"{num}%", value=f"{num / 100:.2f}", default=num==DEFAULT_DENOISE)
                for num in denoise_steps[1:]
            ])
        )
        self.adetailer_denoising_select = ui.Label(
            text="ADetailer Denoise",
            description="How much the face will change.",
            component=ui.Select(options=[
                discord.SelectOption(label=f"{num}%", value=f"{num / 100:.2f}", default=num==DEFAULT_ADETAILER_DENOISE)
                for num in denoise_steps[:-1]
            ])
        )

        self.add_item(self.upscaler_select)
        self.add_item(self.scale_select)
        self.add_item(self.denoising_select)
        self.add_item(self.adetailer_denoising_select)


    async def on_submit(self, interaction: discord.Interaction):
        assert self.parent_interaction.message
        assert isinstance(self.upscaler_select.component, discord.ui.Select)
        assert isinstance(self.scale_select.component, discord.ui.Select)
        assert isinstance(self.denoising_select.component, discord.ui.Select)
        assert isinstance(self.adetailer_denoising_select.component, discord.ui.Select)

        scale = float(self.scale_select.component.values[0])
        denoise = float(self.denoising_select.component.values[0])
        upscaler = self.upscaler_select.component.values[0]
        adetailer_denoising = float(self.adetailer_denoising_select.component.values[0])

        self.payload["scaleFactor"] = scale
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

        if adetailer_denoising > 0:
            self.payload["adetailer"] = deepcopy(ADETAILER_ARGS)
            self.payload["adetailer"]["denoise"] = adetailer_denoising

        await interaction.response.defer(thinking=True)
        self.parent_button.disabled = True
        await self.parent_interaction.message.edit(view=self.parent_view)
        message_content = f"Upscale requested by {interaction.user.mention}"
        await self.generate_image(interaction, payload=self.payload, callback=self.edit_callback(), message_content=message_content)

    async def edit_callback(self):
        await asyncio.sleep(0.1)
        assert self.parent_interaction.message
        self.parent_button.disabled = False
        if not self.parent_view.is_finished():
            try:
                await self.parent_interaction.message.edit(view=self.parent_view)
            except discord.NotFound:
                pass
