import asyncio
from collections import OrderedDict
from typing import Optional

import discord
from redbot.core.bot import Red
from sd_prompt_reader.image_data_reader import ImageDataReader

from aimage.base import AImageBase
from aimage.constants import PARAM_GROUP_REGEX, PARAM_REGEX, PARAMS_BLACKLIST, VIEW_TIMEOUT
from aimage.helpers import delete_button_after


class ImageActions(discord.ui.View):
    def __init__(self, cog: AImageBase, metadata: ImageDataReader, payload: dict, author: discord.Member, channel: discord.abc.Messageable, maxsize: int):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.metadata = metadata
        self.payload = payload
        self.bot: Red = cog.bot
        self.config = cog.config
        self.cache = cog.autocomplete_cache
        self.og_user = author
        self.channel = channel
        self.maxsize = maxsize
        self.generate_image = cog.generate_image

        self.button_caption = discord.ui.Button(emoji='🔎')
        self.button_caption.callback = self.get_caption
        self.button_modify = discord.ui.Button(emoji="🔄")
        self.button_modify.callback = self.modify_image
        self.button_variation = discord.ui.Button(emoji='⏺️')
        self.button_variation.callback = self.variation_image
        self.button_upscale = discord.ui.Button(emoji='⬆')
        self.button_upscale.callback = self.upscale_image
        self.button_delete = discord.ui.Button(emoji='🗑️')
        self.button_delete.callback = self.delete_image

        self.add_item(self.button_caption)
        if not payload.get("enable_hr", False):
            self.add_item(self.button_modify)
            self.add_item(self.button_variation)
            if self.payload["width"]*self.payload["height"]*1.1 < maxsize*maxsize:
                self.add_item(self.button_upscale)
        self.add_item(self.button_delete)


    async def get_caption(self, interaction: discord.Interaction):
        embed = await self.get_params_embed()
        if embed:
            await interaction.response.send_message(embed=embed, ephemeral=True)
            msg = await interaction.original_response()
            asyncio.create_task(delete_button_after(msg))
        else:
            await interaction.response.send_message(f'Parameters for this image:\n```yaml\n{self.metadata}```')


    async def modify_image(self, interaction: discord.Interaction):
        from aimage.views.modify import ModifyModal
        modal = ModifyModal(self)
        await interaction.response.send_modal(modal)


    async def variation_image(self, interaction: discord.Interaction):
        from aimage.views.variation import VariationModal
        modal = VariationModal(self)
        await interaction.response.send_modal(modal)


    async def upscale_image(self, interaction: discord.Interaction):
        from aimage.views.hi_res import HiresModal
        modal = HiresModal(self, interaction, self.maxsize)
        await interaction.response.send_modal(modal)


    async def delete_image(self, interaction: discord.Interaction):
        assert interaction.message
        if not (await self.check_if_can_delete(interaction)):
            return await interaction.response.send_message(content=":warning: Only the requester and members with `Manage Messages` permission can delete this image!", ephemeral=True)

        self.button_delete.disabled = True
        await interaction.message.delete()

        prompt = self.payload["prompt"]
        if interaction.user.id == self.og_user.id:
            await interaction.response.send_message(
                f'{self.og_user.mention} deleted their requested image with prompt: `{prompt}`',
                allowed_mentions=discord.AllowedMentions.none(),
                ephemeral=True)
        else:
            await interaction.response.send_message(
                f'{interaction.user.mention} deleted a image requested by {self.og_user.mention} with prompt: `{prompt}`',
                allowed_mentions=discord.AllowedMentions.none(),
                ephemeral=True)

        self.stop()


    def get_params_dict(self) -> Optional[dict]:
        output_dict = OrderedDict()
        for key, value in self.metadata.parameter.items():
            if len(output_dict) > 24 or any(blacklisted in key for blacklisted in PARAMS_BLACKLIST):
                continue
            output_dict[key] = value
        for key in output_dict:
            if len(output_dict[key]) > 1000:
                output_dict[key] = output_dict[key][:1000] + "..."

        reordered_dict = OrderedDict()
        for key, value in output_dict.items():
            if "Prompt" in key:
                reordered_dict[key] = value
        for key, value in output_dict.items():
            if "Prompt" not in key:
                reordered_dict[key] = value

        return reordered_dict


    async def get_params_embed(self) -> Optional[discord.Embed]:
        params = self.get_params_dict()
        if not params:
            return None
        embed = discord.Embed(title="Image Parameters", color=await self.bot.get_embed_color(self.channel))
        for key, value in params.items():
            embed.add_field(name=key, value=value, inline="Prompt" not in key)
        return embed


    async def check_if_can_delete(self, interaction: discord.Interaction):
        is_og_user = interaction.user.id == self.og_user.id

        assert interaction.guild and interaction.channel
        member = interaction.guild.get_member(interaction.user.id)
        if not member:
            return False
        can_delete = await self.bot.is_owner(member) or interaction.channel.permissions_for(member).manage_messages

        return is_og_user or can_delete
