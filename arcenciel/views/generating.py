import asyncio

import discord
from arcenciel.base import ArcencielBase
from arcenciel.constants import VIEW_TIMEOUT
from arcenciel.schema import QueuedImageGen
from arcenciel.utils import ImageGenError


class _GeneratingControls:
    def _setup_controls(self, cog: ArcencielBase, gen: QueuedImageGen):
        self.cog = cog
        self.gen = gen
        self.button_inspect = discord.ui.Button(emoji='🔎')
        self.button_cancel = discord.ui.Button(emoji="🛑")
        self.button_inspect.callback = self.inspect
        self.button_cancel.callback = self.cancel

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
        self.gen.pending_preview = None
        self.stop()
        if self.gen.id:
            self.cog.queued_images.pop(self.gen.id, None)
            try:
                await self.cog.api.cancel_request(self.gen.id)
            except ImageGenError:
                pass
        embed = discord.Embed(color=await self.cog.bot.get_embed_color(self.gen.channel))
        embed.set_footer(text=self.gen.user.display_name, icon_url=self.gen.user.display_avatar.url)
        embed.description = "❌ Request cancelled" + (
            f" by {interaction.user.mention}" if interaction.user != self.gen.user else "."
        )
        try:
            if isinstance(self, GeneratingV2View):
                self.set_cancelled(embed.description)
                await interaction.message.edit(
                    view=self, attachments=[], allowed_mentions=discord.AllowedMentions.none()
                )
            else:
                await interaction.message.edit(content="", embed=embed, view=None)
            if self.gen.callback:
                asyncio.create_task(self.gen.callback)
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
        is_privileged = (
            interaction.channel.permissions_for(member).manage_messages or await self.cog.bot.is_owner(member)
        )
        return is_requester or is_privileged


class GeneratingView(discord.ui.View, _GeneratingControls):
    def __init__(self, cog: ArcencielBase, gen: QueuedImageGen):
        super().__init__(timeout=VIEW_TIMEOUT)
        self._setup_controls(cog, gen)
        self.add_item(self.button_inspect)
        self.add_item(self.button_cancel)


class GeneratingV2View(discord.ui.LayoutView, _GeneratingControls):
    def __init__(self, cog: ArcencielBase, gen: QueuedImageGen, color: discord.Color):
        super().__init__(timeout=VIEW_TIMEOUT)
        self._setup_controls(cog, gen)
        self.container = discord.ui.Container(accent_color=color)
        self.controls = discord.ui.ActionRow(self.button_inspect, self.button_cancel)
        self.add_item(self.container)
        self.set_progress("Image request sent...")

    def set_progress(self, status: str, progress: str | None = None, eta: str | None = None,
                     step: str | None = None, preview_filename: str | None = None):
        self.container.clear_items()
        self.container.add_item(discord.ui.TextDisplay(status))
        details = []
        if progress:
            details.append(f"**Progress** {progress}")
        if eta:
            details.append(f"**ETA** {eta}")
        if step:
            details.append(f"**Preview step** {step}")
        if details:
            self.container.add_item(discord.ui.TextDisplay("  ·  ".join(details)))
        if preview_filename:
            self.container.add_item(discord.ui.MediaGallery(
                discord.MediaGalleryItem(f"attachment://{preview_filename}", spoiler=True)
            ))
        self.container.add_item(discord.ui.TextDisplay(
            f"-# {discord.utils.escape_markdown(self.gen.user.display_name)}"
        ))
        self.container.add_item(self.controls)

    def set_cancelled(self, status: str):
        self.container.clear_items()
        self.container.add_item(discord.ui.TextDisplay(status))
