import logging
import discord
import humanize
from typing import Optional
from datetime import datetime, timezone
from redbot.core import checks, commands

from arcenciel.base import ArcencielBase
from arcenciel.utils import make_batches, chunk_and_send
from arcenciel.constants import QUOTA_PERIOD

log = logging.getLogger("red.bz_cogs.arcenciel")


class ArcencielSettings(ArcencielBase):

    @commands.command(name="ckpt") # type: ignore
    async def member_checkpoint(self, ctx: commands.Context, *, checkpoint: Optional[str]):
        """
        Set the default image generation checkpoint for yourself
        """
        if checkpoint is None:
            current_checkpoint = await self.config.user(ctx.author).checkpoint()
            await ctx.send(f"Your current default checkpoint is `{current_checkpoint or '(None)'}`")

        elif checkpoint.lower().strip() in ("clear", "reset", "default", "none"):
            await self.config.user(ctx.author).checkpoint.set("")
            return await ctx.send(f"Checkpoint reset")
        
        ckpt_names = self.autocomplete_cache.get("checkpoints") or {}
        if checkpoint not in ckpt_names.keys():
            names = [f"`{name}`" for name in ckpt_names.keys()]
            log.info(names)
            batches = make_batches(names, 10)
            content = f":warning: Checkpoint must be one of:\n" + ",\n".join([", ".join(batch) for batch in batches])
            return await chunk_and_send(ctx, content, do_reply=True)

        if checkpoint:
            await self.config.user(ctx.author).checkpoint.set(ckpt_names[checkpoint])
            await ctx.tick(message="✅ Default checkpoint updated.")

    @commands.group(name="arcenciel", aliases=["arc", "aec"])  # type: ignore
    @commands.guild_only()
    @checks.bot_has_permissions(embed_links=True, add_reactions=True)
    @checks.admin_or_permissions(manage_guild=True)
    async def arcenciel(self, _: commands.Context):
        """ Manage AI Image cog settings. """
        pass

    @arcenciel.command(name="enable")
    async def enable_cmd(self, ctx: commands.Context):
        """
        Enables the generator on this server
        """
        assert ctx.guild
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.tick()

    @arcenciel.command(name="disable")
    async def disable_cmd(self, ctx: commands.Context):
        """
        Disables the generator on this server
        """
        assert ctx.guild
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.tick()

    @arcenciel.command(name="config")
    async def config_cmd(self, ctx: commands.Context):
        """
        Show the current AI Image config
        """
        assert ctx.guild
        config = await self.config.all()

        embed = discord.Embed(title="Arcenciel Config", color=await ctx.embed_color())
        embed.add_field(name="Default Negative Prompt", value=f"`{config['negative_prompt'][:1000]}`", inline=False)
        embed.add_field(name="Default Checkpoint", value=f"`{config['checkpoint']}`")
        embed.add_field(name="Default VAE", value=f"`{config['vae']}`")
        embed.add_field(name="Default Sampler", value=f"`{config['sampler']}`")
        embed.add_field(name="Default Scheduler", value=f"`{config['scheduler']}`")
        embed.add_field(name="Default CFG", value=f"`{config['cfg']}`")
        embed.add_field(name="Default Steps", value=f"`{config['sampling_steps']}`")
        embed.add_field(name="Default Size", value=f"`{config['width']}x{config['height']}`")
        embed.add_field(name="NSFW allowed", value=f"`{config['nsfw']}`")
        embed.add_field(name="Use ADetailer", value=f"`{config['adetailer']}`")
        embed.add_field(name="Max img2img size", value=f"`{config['max_img2img']}`")
        embed.add_field(name="Blacklist regex", value=f"`{config['blacklist_regex'][:1000]}`", inline=False)

        return await ctx.send(embed=embed)

    @arcenciel.command(name="nsfw")
    async def nsfw_cmd(self, ctx: commands.Context):
        """
        Toggles filtering of NSFW images
        """
        assert ctx.guild
        nsfw = await self.config.nsfw()
        await self.config.nsfw.set(not nsfw)
        await ctx.send(f"NSFW filtering is now {'`disabled`' if not nsfw else '`enabled`'}")

    @arcenciel.command(name="negative_prompt", aliases=["negative"])
    async def negative_prompt_cmd(self, ctx: commands.Context, *, negative_prompt: Optional[str]):
        """
        Set the default negative prompt
        """
        assert ctx.guild
        if not negative_prompt:
            negative_prompt = ""
        await self.config.negative_prompt.set(negative_prompt)
        await ctx.tick(message="✅ Default negative prompt updated.")

    @arcenciel.command(name="cfg")
    async def cfg_cmd(self, ctx: commands.Context, cfg: int):
        """
        Set the default cfg
        """
        assert ctx.guild
        await self.config.cfg.set(cfg)
        await ctx.tick(message="✅ Default CFG updated.")

    @arcenciel.command(name="steps")
    async def sampling_steps_cmd(self, ctx: commands.Context, sampling_steps: int):
        """
        Set the default sampling steps
        """
        assert ctx.guild
        await self.config.sampling_steps.set(sampling_steps)
        await ctx.tick(message="✅ Default sampling steps updated.")

    @arcenciel.command(name="sampler")
    async def sampler_cmd(self, ctx: commands.Context, *, sampler: Optional[str]):
        """
        Set the default sampler
        """
        assert ctx.guild

        sampler_names = self.autocomplete_cache.get("samplers") or {}
        if not sampler or sampler not in sampler_names.keys():
            names = [f"`{name}`" for name in sampler_names.keys()]
            return await ctx.send(f":warning: Sampler must be one of: " + ", ".join(names))

        await self.config.sampler.set(sampler_names[sampler])
        await ctx.tick(message="✅ Default sampler updated.")

    @arcenciel.command(name="scheduler")
    async def scheduler_cmd(self, ctx: commands.Context, *, scheduler: Optional[str]):
        """
        Set the default scheduler
        """
        assert ctx.guild

        sch_names = self.autocomplete_cache.get("schedulers") or {}
        if not scheduler or scheduler not in sch_names.keys():
            names = [f"`{name}`" for name in sch_names.keys()]
            return await ctx.send(f":warning: Scheduler must be one of: " + ", ".join(names))

        await self.config.scheduler.set(sch_names[scheduler])
        await ctx.tick(message="✅ Default scheduler updated.")

    @arcenciel.command(name="width")
    async def width_cmd(self, ctx: commands.Context, width: int):
        """
        Set the default width
        """
        assert ctx.guild
        if width < 256 or width > 1536:
            return await ctx.send("Value must range between 256 and 1536.")
        await self.config.width.set(width)
        await ctx.tick(message="✅ Default width updated.")

    @arcenciel.command(name="height")
    async def height_cmd(self, ctx: commands.Context, height: int):
        """
        Set the default height
        """
        assert ctx.guild
        if height < 256 or height > 1536:
            return await ctx.send("Value must range between 256 and 1536.")
        await self.config.height.set(height)
        await ctx.tick(message="✅ Default height updated.")

    @arcenciel.command(name="max_img2img")
    async def max_img2img_cmd(self, ctx: commands.Context, resolution: int):
        """
        Set the maximum size (in pixels squared) of img2img and hires upscale.
        Used to prevent out of memory errors. Default is 1536.
        """
        assert ctx.guild
        if resolution < 512 or resolution > 4096:
            return await ctx.send("Value must range between 512 and 4096.")
        await self.config.max_img2img.set(resolution)
        await ctx.tick(message="✅ Maximum img2img size updated.")

    @arcenciel.command(name="checkpoint", aliases=["model", "ckpt"])
    async def checkpoint_cmd(self, ctx: commands.Context, *, checkpoint: Optional[str]):
        """
        Set the default checkpoint / model used for generating images
        """
        assert ctx.guild
        
        ckpt_names = self.autocomplete_cache.get("checkpoints") or {}
        if not checkpoint or checkpoint not in ckpt_names.keys():
            names = [f"`{name}`" for name in ckpt_names.keys()]
            batches = make_batches(names, 10)
            content = f":warning: Checkpoint must be one of:\n" + ",\n".join([", ".join(batch) for batch in batches])
            return await chunk_and_send(ctx, content, do_reply=True)

        await self.config.checkpoint.set(ckpt_names[checkpoint])
        await ctx.tick(message="✅ Default checkpoint updated.")

    @arcenciel.command(name="vae")
    async def vae_cmd(self, ctx: commands.Context, *, vae: Optional[str]):
        """
        Set the default vae used for generating images
        """
        assert ctx.guild

        vae_names = self.autocomplete_cache.get("vae") or {}
        if not vae or vae not in vae_names.keys():
            names = [f"`{name}`" for name in vae_names.keys()]
            return await ctx.send(f":warning: Vae must be one of: " + ", ".join(names))

        await self.config.vae.set(vae_names[vae])
        await ctx.tick(message="✅ Default VAE updated.")

    @arcenciel.command(name="adetailer")
    async def adetailer_cmd(self, ctx: commands.Context):
        """
        Whether to use face adetailer for all images
        """
        assert ctx.guild
        new = not await self.config.adetailer()
        await self.config.adetailer.set(new)
        await ctx.send(f"ADetailer is now {'`disabled`' if not new else '`enabled`'} for basic gens")

    @arcenciel.command(name="blacklist", aliases=["blocklist"])
    @commands.is_owner()
    async def blacklist_cmd(self, ctx: commands.Context, *, regex: Optional[str]):
        """
        Sets a blacklist regex for prompts
        """
        if not regex or not regex.strip():
            regex = await self.config.blacklist_regex()
            if not regex:
                await ctx.send("No regex set")
            else:
                await ctx.send(f"Current regex\n```re\n{regex}```")
        else:
            await self.config.blacklist_regex.set(regex.strip())
            await ctx.send(f"Set regex\n```re\n{regex.strip()}```")

    @arcenciel.command(name="loading_emoji")
    @commands.is_owner()
    async def loading_emoji_cmd(self, ctx: commands.Context, emoji: str):
        """
        Sets a loading emoji for the progress message
        """
        await self.config.loading_emoji.set(emoji)
        await ctx.tick()

    @arcenciel.command(name="arcenciel_emoji")
    @commands.is_owner()
    async def arcenciel_emoji_cmd(self, ctx: commands.Context, emoji: str):
        """
        Sets the arcenciel emoji for search results
        """
        await self.config.arcenciel_emoji.set(emoji)
        await ctx.tick()

    @arcenciel.command(name="sync")
    @checks.is_owner()
    @checks.bot_in_a_guild()
    async def sync_cmd(self, ctx: commands.Context):
        """
        Updates the autocomplete cache
        """
        assert ctx.guild
        await ctx.message.add_reaction("⏳")
        await self.update_autocomplete_cache()
        await ctx.message.add_reaction("✅")
        await ctx.message.remove_reaction("⏳", ctx.guild.me)
        
    @arcenciel.group(name="quota", aliases=["vip", "limit", "limits"])
    @checks.is_owner()
    @checks.bot_in_a_guild()
    async def quota_cmd(self, _: commands.Context):
        """
        Manage image generation limits for various groups
        """
        pass

    @quota_cmd.command(name="list", aliases=["show", "roles", "limit", "limits", "all"])
    async def quota_list_cmd(self, ctx: commands.Context):
        """
        Lists all image generation limits
        """
        assert ctx.guild
        role_configs = await self.config.all_roles()
        entries = [(role, config["quota"])
                   for role_id, config in role_configs.items()
                   if (role := ctx.guild.get_role(role_id)) and config.get("quota", 0) > 0]

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx.channel), title="Generation Quotas")
        if not entries:
            embed.description = "No roles currently have a generation quota configured."
            return await ctx.send(embed=embed)

        entries.sort(key=lambda entry: entry[1], reverse=True)
        period_str = humanize.precisedelta(QUOTA_PERIOD)
        lines = [f"{role.mention} - {quota} images / {period_str}" for role, quota in entries]
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @quota_cmd.command(name="set", aliases=["add", "role"])
    async def quota_set_cmd(self, ctx: commands.Context, role: discord.Role, limit: int):
        """
        Set the image generation limit of a server role
        """
        if limit < 0:
            return await ctx.send(":warning: The quota limit cannot be negative.")

        await self.config.role(role).quota.set(limit)

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx.channel))
        if limit == 0:
            embed.description = f"✅ {role.mention} no longer has a generation quota (limit set to 0)."
        else:
            period_str = humanize.precisedelta(QUOTA_PERIOD, suppress=["seconds"], format="%01d")
            embed.description = f"✅ {role.mention} now has a generation quota of **{limit}** images per {period_str}."
        await ctx.send(embed=embed)

    @quota_cmd.command(name="check", aliases=["user"])
    async def quota_check_cmd(self, ctx: commands.Context, user: discord.Member):
        """
        Check the current progress and limit of a user's quota
        """
        assert ctx.guild
        role_configs = await self.config.all_roles()
        role_quotas: dict[int, int] = {role_id: config["quota"] for role_id, config in role_configs.items()}

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx.channel))
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)

        highest_quota = max([role_quotas.get(role.id, 0) for role in user.roles], default=0)
        if highest_quota <= 0:
            embed.description = f"{user.mention} is not currently authorized to use the generator."
            return await ctx.send(embed=embed)

        quota_progress: int = await self.config.user(user).quota_progress()
        quota_start = datetime.fromisoformat(await self.config.user(user).quota_start())
        now = datetime.now(timezone.utc)
        quota_elapsed = (now - quota_start).total_seconds()
        if quota_elapsed > QUOTA_PERIOD:
            quota_progress = 0
            remaining_str = "Refreshes on next use"
        else:
            remaining = QUOTA_PERIOD - quota_elapsed
            remaining_str = humanize.precisedelta(remaining, suppress=["seconds"] if remaining > 3600 else [], format="%02d")

        embed.add_field(name="Progress", value=f"{quota_progress} / {highest_quota}")
        embed.add_field(name="Time remaining", value=remaining_str)
        await ctx.send(embed=embed)

    @quota_cmd.command(name="reset", aliases=["clear"])
    async def quota_reset_cmd(self, ctx: commands.Context, user: discord.User):
        """
        Reset a user's image generation quota progress
        """
        now = datetime.now(timezone.utc)
        await self.config.user(user).quota_progress.set(0)
        await self.config.user(user).quota_start.set(now.isoformat())

        embed = discord.Embed(color=await self.bot.get_embed_color(ctx.channel))
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.description = f"✅ {user.mention}'s generation quota has been reset."
        await ctx.send(embed=embed)
