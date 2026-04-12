import logging
import discord
from typing import Optional
from redbot.core import checks, commands

from aimage.base import AImageBase
from aimage.utils import make_batches, chunk_and_send

log = logging.getLogger("red.bz_cogs.aimage")


class AImageSettings(AImageBase):

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
            names = [f"`{name}`" for name in ckpt_names.keys()]
            batches = make_batches(names, 10)
            content = f":warning: Checkpoint must be one of:\n" + ",\n".join([", ".join(batch) for batch in batches])
            return await chunk_and_send(ctx, content, do_reply=True)

        await self.config.user(ctx.author).checkpoint.set(ckpt_names[checkpoint])
        await ctx.tick(message="✅ Default checkpoint updated.")

    @commands.group(name="aimage") # type: ignore
    @commands.guild_only()
    @checks.bot_has_permissions(embed_links=True, add_reactions=True)
    @checks.admin_or_permissions(manage_guild=True)
    async def aimage(self, _: commands.Context):
        """ Manage AI Image cog settings for this server """
        pass

    @aimage.command(name="enable")
    async def enable_cmd(self, ctx: commands.Context):
        """
        Enables the generator on this server
        """
        assert ctx.guild
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.tick()

    @aimage.command(name="disable")
    async def disable_cmd(self, ctx: commands.Context):
        """
        Disables the generator on this server
        """
        assert ctx.guild
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.tick()

    @aimage.command(name="config")
    async def config_cmd(self, ctx: commands.Context):
        """
        Show the current AI Image config
        """
        assert ctx.guild
        config = await self.config.all()

        embed = discord.Embed(title="AImage Config", color=await ctx.embed_color())
        embed.add_field(name="Default Negative Prompt", value=f"`{config['negative_prompt'][1000]}`", inline=False)
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
        embed.add_field(name="Blacklist regex", value=f"`{config['blacklist_regex'][1000]}`", inline=False)

        return await ctx.send(embed=embed)

    @aimage.command(name="nsfw")
    async def nsfw_cmd(self, ctx: commands.Context):
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
    async def negative_prompt_cmd(self, ctx: commands.Context, *, negative_prompt: Optional[str]):
        """
        Set the default negative prompt
        """
        assert ctx.guild
        if not negative_prompt:
            negative_prompt = ""
        await self.config.negative_prompt.set(negative_prompt)
        await ctx.tick(message="✅ Default negative prompt updated.")

    @aimage.command(name="cfg")
    async def cfg_cmd(self, ctx: commands.Context, cfg: int):
        """
        Set the default cfg
        """
        assert ctx.guild
        await self.config.cfg.set(cfg)
        await ctx.tick(message="✅ Default CFG updated.")

    @aimage.command(name="steps")
    async def sampling_steps_cmd(self, ctx: commands.Context, sampling_steps: int):
        """
        Set the default sampling steps
        """
        assert ctx.guild
        await self.config.sampling_steps.set(sampling_steps)
        await ctx.tick(message="✅ Default sampling steps updated.")

    @aimage.command(name="sampler")
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

    @aimage.command(name="scheduler")
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

    @aimage.command(name="width")
    async def width_cmd(self, ctx: commands.Context, width: int):
        """
        Set the default width
        """
        assert ctx.guild
        if width < 256 or width > 1536:
            return await ctx.send("Value must range between 256 and 1536.")
        await self.config.width.set(width)
        await ctx.tick(message="✅ Default width updated.")

    @aimage.command(name="height")
    async def height_cmd(self, ctx: commands.Context, height: int):
        """
        Set the default height
        """
        assert ctx.guild
        if height < 256 or height > 1536:
            return await ctx.send("Value must range between 256 and 1536.")
        await self.config.height.set(height)
        await ctx.tick(message="✅ Default height updated.")

    @aimage.command(name="max_img2img")
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

    @aimage.command(name="checkpoint", aliases=["model"])
    async def checkpoint_cmd(self, ctx: commands.Context, *, checkpoint: Optional[str]):
        """
        Set the default checkpoint / model used for generating images.
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

    @aimage.command(name="vae")
    async def vae_cmd(self, ctx: commands.Context, *, vae: Optional[str]):
        """
        Set the default vae used for generating images.
        """
        assert ctx.guild

        vae_names = self.autocomplete_cache.get("vae") or {}
        if not vae or vae not in vae_names.keys():
            names = [f"`{name}`" for name in vae_names.keys()]
            return await ctx.send(f":warning: Vae must be one of: " + ", ".join(names))

        await self.config.vae.set(vae_names[vae])
        await ctx.tick(message="✅ Default VAE updated.")

    @aimage.command(name="adetailer")
    async def adetailer_cmd(self, ctx: commands.Context):
        """
        Whether to use face adetailer, which improves quality.
        """
        assert ctx.guild
        new = not await self.config.adetailer()
        await self.config.adetailer.set(new)
        await ctx.send(f"ADetailer is now {'`disabled`' if not new else '`enabled`'} for basic gens")

    @aimage.command(name="blacklist", aliases=["blocklist"])
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

    @aimage.command(name="loading_emoji")
    @commands.is_owner()
    async def loading_emoji_cmd(self, ctx: commands.Context, emoji: str):
        """
        Sets a loading emoji for the progress message
        """
        await self.config.loading_emoji.set(emoji)
        await ctx.tick()

    @aimage.command(name="arcenciel_emoji")
    @commands.is_owner()
    async def arcenciel_emoji_cmd(self, ctx: commands.Context, emoji: str):
        """
        Sets the arcenciel emoji for search results
        """
        await self.config.arcenciel_emoji.set(emoji)
        await ctx.tick()

    @aimage.command(name="sync")
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
        
    @aimage.group(name="vip")
    @checks.is_owner()
    @checks.bot_in_a_guild()
    async def vip_cmd(self, _: commands.Context):
        """
        Manage the VIP role for image generation, which can generate as many images at the same time as they want.
        """
        pass

    @vip_cmd.command(name="quota")
    async def vip_quota(self, ctx: commands.Context, gens: int):
        """
        Sets the number of gens a user can do per hour
        """
        if gens < 0 or gens > 1000:
            return await ctx.send("Valid quota values range from 0 to 1000")
        await self.config.quota.set(gens)
        await ctx.send(f"Hourly quota set to {gens}")

    @vip_cmd.command(name="view")
    async def vip_view(self, ctx: commands.Context):
        """
        View the VIP role
        """
        assert ctx.guild
        role_id = await self.config.guild(ctx.guild).vip_role()
        all_users = await self.config.all_users()
        users = [f"<@{uid}>" for uid, config in all_users.items() if config.get("vip")]
        content = "`VIP role for this guild:` " + (f"<@&{role_id}>" if role_id and role_id >= 0 else "*none*")
        content += "\n`VIP users globally:` " + (" ".join(users) if users else "*none*")
        await ctx.send(content, allowed_mentions=discord.AllowedMentions.none())

    @vip_cmd.command(name="role")
    async def vip_role(self, ctx: commands.Context, *, role: discord.Role):
        """
        Sets a VIP role for this server
        """
        assert ctx.guild
        await self.config.guild(ctx.guild).vip_role.set(role.id)
        await ctx.send(f"VIP role set to {role.mention}", allowed_mentions=discord.AllowedMentions.none())

    @vip_cmd.command(name="user")
    async def vip_user(self, ctx: commands.Context, *, user: discord.User):
        """
        Toggles whether a user is VIP
        """
        new = not await self.config.user(user).vip() 
        await self.config.user(user).vip.set(new)
        await ctx.send(f"User {user.mention} is {'now VIP' if new else 'no longer VIP'}", allowed_mentions=discord.AllowedMentions.none())
