import logging
import discord
from redbot.core.bot import Red

from aimage.base import AImageBase
from aimage.comfy import ComfyMetadata
from aimage.constants import VIEW_TIMEOUT

log = logging.getLogger("red.holo-cogs.aimage")


class ImageActions(discord.ui.View):
    def __init__(self, cog: AImageBase, metadata: ComfyMetadata, payload: dict, author: discord.Member, channel: discord.abc.Messageable, maxsize: int):
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
        if not payload.get("upscaleProfiles", False):
            self.add_item(self.button_modify)
            self.add_item(self.button_variation)
            if self.payload["width"]*self.payload["height"]*1.1 < maxsize*maxsize:
                self.add_item(self.button_upscale)
        self.add_item(self.button_delete)


    async def get_caption(self, interaction: discord.Interaction):
        embed = await self.get_params_embed()
        if embed:
            await interaction.response.send_message(embed=embed, ephemeral=True)
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
        if not self.cache.get("upscale"):
            return await interaction.response.send_message(content=":warning: Upscaling is not available at this time. Please contact the bot owner.", ephemeral=True)
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

        self.stop)()


    async def get_params_embed(self) -> discord.Embed | None:
        params = self.metadata.as_dict()
        if not params:
            return None
        
        embed = discord.Embed(title="Image Parameters", color=await self.bot.get_embed_color(self.channel))
        for key in params.keys():
            if len(str(params[key])) > 1000:
                params[key] = str(params[key])[:997] + "..."
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
