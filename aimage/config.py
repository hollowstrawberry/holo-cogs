import asyncio
import logging
from typing import Optional

import discord
from redbot.core import checks, commands
from redbot.core.utils.menus import SimpleMenu # type: ignore

from aimage.base import AImageBase
from aimage.helpers import delete_button_after

log = logging.getLogger("red.bz_cogs.aimage")


class AImageConfig(AImageBase):

    @commands.command(name="ckpt") # type: ignore
    async def member_checkpoint(self, ctx: commands.Context, *, checkpoint: Optional[str]):
        """
        Set the default checkpoint for yourself in this guild.
        """
        if checkpoint is None:
            checkpoint = await self.config.user(ctx.author).checkpoint()
            return await ctx.send(f"Your current default checkpoint is `{checkpoint or '(None)'}`")

        if checkpoint.lower().strip() in ("clear", "reset", "default"):
            await self.config.user(ctx.author).checkpoint.set("")
            return await ctx.send(f"Checkpoint reset")
        
        ckpt_names = self.autocomplete_cache.get("checkpoints") or {}
        if checkpoint not in ckpt_names.keys():
            return await ctx.send(f":warning: Invalid checkpoint. Pick one of these:\n`{', '.join(list(ckpt_names.keys()))}`"[:2000])

        await self.config.user(ctx.author).checkpoint.set(ckpt_names[checkpoint])
        await ctx.tick(message="✅ Default checkpoint updated.")

    @commands.group(name="aimage") # type: ignore
    @commands.guild_only()
    @checks.bot_has_permissions(embed_links=True, add_reactions=True)
    @checks.admin_or_permissions(manage_guild=True)
    async def aimage(self, _: commands.Context):
        """ Manage AI Image cog settings for this server """
        pass

    @aimage.command(name="config")
    async def config_cmd(self, ctx: commands.Context):
        """
        Show the current AI Image config
        """
        assert ctx.guild
        config = await self.config.all()

        embed = discord.Embed(title="AImage Config", color=await ctx.embed_color())

        negative_prompt = config["negative_prompt"]
        if len(negative_prompt) > 1000:
            negative_prompt = negative_prompt[:1000] + "..."
        embed.add_field(name="Default Negative Prompt", value=f"`{negative_prompt}`", inline=False)

        embed.add_field(name="Default Checkpoint", value=f"`{config['checkpoint']}`")
        embed.add_field(name="Default VAE", value=f"`{config['vae']}`")
        embed.add_field(name="Default Sampler", value=f"`{config['sampler']}`")
        embed.add_field(name="Default CFG", value=f"`{config['cfg']}`")
        embed.add_field(name="Default Steps", value=f"`{config['sampling_steps']}`")
        embed.add_field(name="Default Size", value=f"`{config['width']}x{config['height']}`")
        embed.add_field(name="NSFW allowed", value=f"`{config['nsfw']}`")
        embed.add_field(name="Use ADetailer", value=f"`{config['adetailer']}`")
        embed.add_field(name="Use Tiled VAE", value=f"`{config['tiledvae']}`")
        embed.add_field(name="Max img2img size", value=f"`{config['max_img2img']}`²")
        embed.add_field(name="Blacklist regex", value=f"`{config['blacklist_regex']}`", inline=False)

        return await ctx.send(embed=embed)

    @aimage.command(name="nsfw")
    async def nsfw(self, ctx: commands.Context):
        """
        Toggles filtering of NSFW images (A1111 only)
        """
        assert ctx.guild
        nsfw = await self.config.nsfw()
        if nsfw:
            await ctx.message.add_reaction("🔄")
            data = self.autocomplete_cache.get("scripts") or []
            await ctx.message.remove_reaction("🔄", ctx.me)
            if "censorscript" not in data:
                return await ctx.send(":warning: sd-webui-nsfw-checker is not installed in webui, install <https://github.com/hollowstrawberry/sd-webui-nsfw-checker>")

        await self.config.nsfw.set(not nsfw)
        await ctx.send(f"NSFW filtering is now {'`disabled`' if not nsfw else '`enabled`'}")

    @aimage.command(name="negative_prompt")
    async def negative_prompt(self, ctx: commands.Context, *, negative_prompt: Optional[str]):
        """
        Set the default negative prompt
        """
        assert ctx.guild
        if not negative_prompt:
            negative_prompt = ""
        await self.config.negative_prompt.set(negative_prompt)
        await ctx.tick(message="✅ Default negative prompt updated.")

    @aimage.command(name="cfg")
    async def cfg(self, ctx: commands.Context, cfg: int):
        """
        Set the default cfg
        """
        assert ctx.guild
        await self.config.cfg.set(cfg)
        await ctx.tick(message="✅ Default CFG updated.")

    @aimage.command(name="steps")
    async def sampling_steps(self, ctx: commands.Context, sampling_steps: int):
        """
        Set the default sampling steps
        """
        assert ctx.guild
        await self.config.sampling_steps.set(sampling_steps)
        await ctx.tick(message="✅ Default sampling steps updated.")

    @aimage.command(name="sampler")
    async def sampler(self, ctx: commands.Context, *, sampler: str):
        """
        Set the default sampler
        """
        assert ctx.guild

        sampler_names = self.autocomplete_cache.get("samplers") or {}
        if sampler not in sampler_names.keys():
            return await ctx.send(f":warning: Sampler must be one of: `{', '.join(list(sampler_names.keys()))}`"[:2000])

        await self.config.sampler.set(sampler_names[sampler])
        await ctx.tick(message="✅ Default sampler updated.")

    @aimage.command(name="scheduler")
    async def scheduler(self, ctx: commands.Context, *, scheduler: str):
        """
        Set the default scheduler
        """
        assert ctx.guild

        sch_names = self.autocomplete_cache.get("schedulers") or {}
        if scheduler not in sch_names.keys():
            return await ctx.send(f":warning: scheduler must be one of: `{', '.join(list(sch_names.keys()))}`"[:2000])

        await self.config.scheduler.set(sch_names[scheduler])
        await ctx.tick(message="✅ Default scheduler updated.")

    @aimage.command(name="width")
    async def width(self, ctx: commands.Context, width: int):
        """
        Set the default width
        """
        assert ctx.guild
        if width < 256 or width > 1536:
            return await ctx.send("Value must range between 256 and 1536.")
        await self.config.width.set(width)
        await ctx.tick(message="✅ Default width updated.")

    @aimage.command(name="height")
    async def height(self, ctx: commands.Context, height: int):
        """
        Set the default height
        """
        assert ctx.guild
        if height < 256 or height > 1536:
            return await ctx.send("Value must range between 256 and 1536.")
        await self.config.height.set(height)
        await ctx.tick(message="✅ Default height updated.")

    @aimage.command(name="max_img2img")
    async def max_img2img(self, ctx: commands.Context, resolution: int):
        """
        Set the maximum size (in pixels squared) of img2img and hires upscale.
        Used to prevent out of memory errors. Default is 1536.
        """
        assert ctx.guild
        if resolution < 512 or resolution > 4096:
            return await ctx.send("Value must range between 512 and 4096.")
        await self.config.max_img2img.set(resolution)
        await ctx.tick(message="✅ Maximum img2img size updated.")

    @aimage.command(name="checkpoint", aliases=["model"])
    async def checkpoint(self, ctx: commands.Context, *, checkpoint: str):
        """
        Set the default checkpoint / model used for generating images.
        """
        assert ctx.guild
        
        ckpt_names = self.autocomplete_cache.get("checkpoints") or {}
        if checkpoint not in ckpt_names.keys():
            return await ctx.send(f":warning: Invalid checkpoint. Pick one of these:\n`{', '.join(list(ckpt_names.keys()))}`"[:2000])

        await self.config.checkpoint.set(ckpt_names[checkpoint])
        await ctx.tick(message="✅ Default checkpoint updated.")

    @aimage.command(name="vae")
    async def vae(self, ctx: commands.Context, *, vae: str):
        """
        Set the default vae used for generating images.
        """
        assert ctx.guild

        vae_names = self.autocomplete_cache.get("vaes") or {}
        if vae not in vae_names.keys():
            return await ctx.send(f":warning: Invalid vae. Pick one of these:\n`{', '.join(list(vae_names.keys()))}`"[:2000])

        await self.config.vae.set(vae_names[vae])
        await ctx.tick(message="✅ Default VAE updated.")

    @aimage.command(name="adetailer")
    async def adetailer(self, ctx: commands.Context):
        """
        Whether to use face adetailer, which improves quality.
        """
        assert ctx.guild
        new = not await self.config.adetailer()
        await self.config.adetailer.set(new)
        await ctx.send(f"ADetailer is now {'`disabled`' if not new else '`enabled`'}")

    @aimage.command(name="blacklist")
    @commands.is_owner()
    async def blacklist_regex(self, ctx: commands.Context, *, regex: Optional[str]):
        """
        Sets a blacklist regex for prompts
        """
        assert ctx.guild
        if not regex or not regex.strip():
            regex = await self.config.blacklist_regex()
            if not regex:
                await ctx.send("No regex set")
            else:
                await ctx.send(f"Current regex\n```re\n{regex}```")
        else:
            await self.config.blacklist_regex.set(regex.strip())
            await ctx.send(f"Set regex\n```re\n{regex.strip()}```")

    @aimage.command()
    @checks.is_owner()
    @checks.bot_in_a_guild()
    async def sync(self, ctx: commands.Context):
        """
        Updates the autocomplete cache
        """
        assert ctx.guild
        await ctx.message.add_reaction("⏳")
        await self.update_autocomplete_cache()
        await ctx.message.add_reaction("✅")
        await ctx.message.remove_reaction("⏳", ctx.guild.me)
        
    @aimage.group()
    @checks.is_owner()
    @checks.bot_in_a_guild()
    async def vip(self, _: commands.Context):
        """
        Manage the VIP role for image generation, which can generate as many images at the same time as they want.
        """
        pass

    @vip.command(name="view")
    async def vip_view(self, ctx: commands.Context):
        """
        View the VIP role
        """
        assert ctx.guild
        role_id = await self.config.guild(ctx.guild).vip_role()
        role = ctx.guild.get_role(role_id)
        if role:
            await ctx.send(f"Current VIP role is {role.mention}", allowed_mentions=discord.AllowedMentions.none())
        else:
            await ctx.send("No VIP role set.")

    @vip.command(name="set", aliases=["role"])
    async def vip_set(self, ctx: commands.Context, role: discord.Role):
        """
        View the VIP role
        """
        assert ctx.guild
        await self.config.guild(ctx.guild).vip_role.set(role.id)
        await ctx.send(f"VIP role set to {role.mention}", allowed_mentions=discord.AllowedMentions.none())
