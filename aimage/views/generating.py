import asyncio
import discord
import discord.ui as ui

from aimage.base import AImageBase
from aimage.utils import ImageGenError
from aimage.schema import QueuedImageGen
from aimage.constants import VIEW_TIMEOUT


class GeneratingView(ui.View):
    def __init__(self, cog: AImageBase, gen: QueuedImageGen):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.gen = gen
        self.button_inspect = discord.ui.Button(emoji='🔎')
        self.button_cancel = discord.ui.Button(emoji="🛑")
        self.button_inspect.callback = self.inspect
        self.button_cancel.callback = self.cancel
        self.add_item(self.button_inspect)
        self.add_item(self.button_cancel)

    async def inspect(self, interaction: discord.Interaction):
        embed = discord.Embed(color=await self.cog.bot.get_embed_color(self.gen.channel))
        embed.title = "Image Request"
        prompt = self.gen.payload.get("prompt") or "*unknown*"
        negative_prompt = self.gen.payload.get("negativePrompt") or "*unknown*"
        if len(prompt) > 1000:
            prompt = prompt[:997] + "..."
        if len(negative_prompt) > 1000:
            negative_prompt = negative_prompt[:997] + "..."
        embed.add_field(name="Prompt", value=prompt, inline=False)
        embed.add_field(name="Negative Prompt", value=negative_prompt, inline=False)
        embed.set_footer(text=self.gen.user.display_name, icon_url=self.gen.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cancel(self, interaction: discord.Interaction):
        if not await self.check_if_can_cancel(interaction):
            return await interaction.response.send_message(
                content=f":warning: Only {self.gen.user.mention} and moderators can cancel this request!",
                allowed_mentions=discord.AllowedMentions.none(),
                ephemeral=True,
            )
        assert self.cog.api and interaction.message
        self.gen.cancelled = True
        self.stop()
        if self.gen.id:
            self.cog.queued_images.pop(self.gen.id, None)
            try:
                await self.cog.api.cancel_request(self.gen.id)
            except ImageGenError:
                pass
        embed = discord.Embed(color=await self.cog.bot.get_embed_color(self.gen.channel))
        embed.set_footer(text=self.gen.user.display_name, icon_url=self.gen.user.display_avatar.url)
        embed.description = f"❌ Request cancelled" + (f" by {interaction.user.mention}" if interaction.user != self.gen.user else ".")
        try:
            await interaction.message.edit(content="", embed=embed, view=None)
            await asyncio.sleep(5)
            await interaction.message.delete()
        except discord.NotFound:
            pass

    async def check_if_can_cancel(self, interaction: discord.Interaction):
        assert interaction.guild and interaction.channel
        member = interaction.guild.get_member(interaction.user.id)
        if not member:
            return False
        is_requester = interaction.user.id == self.gen.user.id
        is_privileged = interaction.channel.permissions_for(member).manage_messages or await self.cog.bot.is_owner(member)
        return is_requester or is_privileged
