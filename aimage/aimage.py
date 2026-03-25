import re
import logging
import asyncio
import aiohttp
import discord
from io import BytesIO
from copy import copy
from typing import Coroutine, List, Optional, Union
from datetime import datetime, timezone
from rapidfuzz import fuzz
from sd_prompt_reader.image_data_reader import ImageDataReader

from discord.ext import tasks
from redbot.core import app_commands, checks, commands

from aimage.arcenciel_api import ArcEnCielAPI
from aimage.comfy import ComfyMetadataReader
from aimage.constants import ENDPOINT, EXCLUDE_TAGGER, SUPPORTED_IMAGE_TYPES, PROGRESS_UPDATE_PERIOD
from aimage.utils import ImageGenError, delete_button_after, is_nsfw, send_response, clean_tag, clean_model
from aimage.schema import ImageGenParams, QueuedImageGen
from aimage.config import AImageConfig
from aimage.views.image_actions import ImageActions

log = logging.getLogger("red.holo-cogs.aimage")


class AImage(AImageConfig):
    """ Generate AI images using a A1111 endpoint """

    def __init__(self, bot):
        super().__init__(bot)
        self.api: Optional[ArcEnCielAPI] = None

        default_global = {
            "nsfw": True,
            "quota": 5,
            "blacklist_regex": "",
            "negative_prompt": "worst quality, low quality",
            "cfg": 5,
            "sampling_steps": 24,
            "sampler": "euler_ancestral",
            "checkpoint": None,
            "vae": None,
            "adetailer": False,
            "width": 1024,
            "height": 1024,
            "max_img2img": 2048,
            "scheduler": "normal",
        }
        default_guild = {
            "enabled": False,
            "vip_role": -1,
        }
        default_user = {
            "vip": False,
            "checkpoint": "",
        }
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_user)
        self.config.register_global(**default_global)

    async def cog_load(self):
        await self.bot.wait_until_red_ready()
        api_key = (await self.bot.get_shared_api_tokens("arcenciel")).get("api_key", "")
        self.api = ArcEnCielAPI(self, ENDPOINT, api_key)
        asyncio.create_task(self.update_autocomplete_cache())
        self.consume_queue.start()
        self.clear_quota.start()

    async def cog_unload(self):
        if self.consume_queue.is_running():
            self.consume_queue.stop()
        if self.clear_quota.is_running():
            self.clear_quota.cancel()
        if self.api:
            await self.api.session.close()

    async def update_autocomplete_cache(self):
        assert self.api
        return await self.api.update_autocomplete_cache()

    @tasks.loop(hours=1)
    async def clear_quota(self):
        self.gen_count.clear()
        self.last_quota_refresh = datetime.now(timezone.utc)
        log.info("Refreshed hourly quota")

    @tasks.loop(seconds=1, reconnect=True)
    async def consume_queue(self):
        assert self.api
        if not self.queued_images:
            return
        jobs = await self.api.fetch_queue()
        for job in jobs:
            if job["id"] in self.queued_images:
                gen = self.queued_images[job["id"]]
                if job["status"] in ["completed", "failed"]:
                    del self.queued_images[job["id"]]
                    nsfw = list(job["safety"]["outputs"].values())[0]["rating"] in ["sensitive", "explicit"]
                    error_message = None
                    if job["status"] == "failed":
                        error_message = f"`Reason: {job['safety']['reason'] or 'none'}`" f"`Error: {job['safety']['error'] or 'none'}`"
                    asyncio.create_task(self.finalize_image_generation(gen, nsfw, error_message))
                elif job["status"] in ["queued", "running"] and isinstance(gen.context, discord.Interaction):
                    now = datetime.now(timezone.utc)
                    if (now - gen.last_updated).total_seconds() < PROGRESS_UPDATE_PERIOD:
                        continue
                    gen.last_updated = now
                    percent = job.get("progress", {}).get("percent")
                    eta = job.get("progress", {}).get("etaMs")
                    content = f"{percent=} {eta=}"
                    log.info(content)
                    asyncio.create_task(gen.context.edit_original_response(content=content))

    async def generate_image(self,
                             context: Union[commands.Context, discord.Interaction],
                             payload: Optional[dict] = None,
                             params: ImageGenParams = None,
                             callback: Optional[Coroutine] = None,
                             message_content: Optional[str] = None):
        
        user = context.user if isinstance(context, discord.Interaction) else context.author
        channel = context.channel
        assert self.api and context.guild and isinstance(user, discord.Member) and isinstance(channel, discord.abc.Messageable)
        assert payload or params
        payload = payload or await self.api.build_image_payload(params, user, is_nsfw(channel))  # type: ignore

        enabled = await self.config.guild(context.guild).enabled()
        if not enabled:
            return await send_response(context, content=":warning: The generator is not enabled for this server.")
        
        vip_role = await self.config.guild(context.guild).vip_role()
        is_vip = await self.config.user(user).vip() or any(role.id == vip_role for role in user.roles)
        quota = await self.config.quota()
        has_ongoing_gen = any(gen.user == user for gen in self.queued_images.values())
        elapsed_last_refresh = (datetime.now(timezone.utc) - self.last_quota_refresh).total_seconds()
        if not is_vip:
            if has_ongoing_gen:
                content = "🕒 You must wait for your current image to finish generating before you can request a new one."
                return await send_response(context, content=content, ephemeral=True)
            elif self.gen_count[user.id] >= quota:
                if quota == 0:
                    content = ":warning: You are not authorized to use the generator at this time. You may be interested in the <https://arcenciel.io> generator."
                else:
                    content = "🕒 You have met your generation quota. You can wait for it to refresh, or try the <https://arcenciel.io> generator." \
                            + f"\n\nTime remaining: {int(60 - (elapsed_last_refresh // 60))} minutes."
                return await send_response(context, content=content, ephemeral=True)

        prompt = params.prompt if params else payload.get("prompt", "")

        if await self.contains_blacklisted_word(prompt):
            return await send_response(context, content=":warning: Blocked prompt.")

        if isinstance(context, commands.Context):
            await context.message.add_reaction("⏳")

        try:
            if params and params.image:
                path = await self.api.upload_image(params.image, params.image_filename or "image.png")
                payload["imagePath"] = path
            job = await self.api.request_image(context, payload)
            self.queued_images[job["id"]] = QueuedImageGen(
                job["id"],
                payload,
                user,
                channel,
                context,
                callback,
                message_content,
                datetime.now(timezone.utc)
            )
        except ImageGenError as error:
            content = f":warning: The image couldn't be generated. ({error})"
            asyncio.create_task(send_response(context, content=content))
        except aiohttp.ClientResponseError as error:
            content = f":warning: There was a problem generating the image! `{error.message}`"
            asyncio.create_task(send_response(context, content=content))
            log.exception("Queueing image")
        except Exception as error:
            content = f":warning: There was a problem generating the image! `{type(error).__name__}: {error}`"
            asyncio.create_task(send_response(context, content=content))
            log.exception("Queueing image")


    async def finalize_image_generation(self, gen: QueuedImageGen, nsfw: bool, error_message: Optional[str]):
        assert self.api and isinstance(gen.context, (commands.Context, discord.Interaction))

        if error_message:
            content = f":warning: Failed to generate image. {error_message}"
            return await send_response(gen.context, content=content)
        
        try:
            image_bytes = await self.api.download_image(gen.id)
            metadata_reader = await asyncio.to_thread(ImageDataReader, BytesIO(image_bytes))
            metadata = ComfyMetadataReader.from_info(metadata_reader._info)
            file_id = gen.context.id if isinstance(gen.context, discord.Interaction) else gen.context.message.id
            file = discord.File(BytesIO(image_bytes), filename=f"image_{file_id}.png", spoiler=nsfw)
            maxsize = await self.config.max_img2img()
            view = ImageActions(self, metadata, gen.payload, gen.user, gen.channel, maxsize)
            content = f"-# {gen.message_content}" if gen.message_content else None
            msg = await send_response(gen.context, file=file, view=view, content=content, allowed_mentions=discord.AllowedMentions.none())
        except ImageGenError as error:
            content = f":warning: Failed to retrieve image. ({error})"
            asyncio.create_task(send_response(gen.context, content=content))
            return
        except aiohttp.ClientResponseError as error:
            content = f":warning: Failed to retrieve image! `{error.message}`"
            asyncio.create_task(send_response(gen.context, content=content))
            raise
        except Exception as error:
            content = f":warning: Failed to retrieve image! `{type(error).__name__}: {error}`"
            asyncio.create_task(send_response(gen.context, content=content))
            raise
        finally:
            if gen.callback:
                asyncio.create_task(gen.callback)

        self.gen_count[gen.user.id] += 1
        asyncio.create_task(self.api.close_request(gen.id))
        asyncio.create_task(delete_button_after(msg))
        imagescanner = self.bot.get_cog("ImageScanner")
        if imagescanner:
            if gen.channel.id in imagescanner.scan_channels:  # type: ignore
                imagescanner.image_cache[msg.id] = ({0: metadata_reader}, {0: image_bytes})  # type: ignore
                asyncio.create_task(msg.add_reaction("🔎"))


    async def build_autocomplete_choices(self, current: str, choices: dict) -> List[app_commands.Choice[str]]:
        if not choices:
            return []
        choices = self.filter_names(choices, current)
        return [app_commands.Choice(name=display_name.replace(".safetensors", ""), value=name.replace(".safetensors", ""))
                for display_name, name in choices.items()
                if len(name.replace(".safetensors", "")) <= 100
                ][:25]

    async def samplers_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        choices = self.autocomplete_cache.get("samplers") or {}
        return await self.build_autocomplete_choices(current, choices)

    async def checkpoint_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        choices = self.autocomplete_cache.get("checkpoints") or {}
        return await self.build_autocomplete_choices(current, choices)

    async def vae_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        choices = self.autocomplete_cache.get("vae") or {}
        return await self.build_autocomplete_choices(current, choices)
    
    async def loras_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        if len(current) < 3:
            return []
        assert self.api
        results = await self.api.search_loras(current)
        return [app_commands.Choice(name=clean_model(name.replace(".safetensors", "")), value=name.replace(".safetensors", ""))
                for name in results
                if len(name.replace(".safetensors", "")) <= 100
                ][:25]
    

    _parameter_descriptions = {
        "prompt": "The prompt to generate an image from.",
        "negative_prompt": "Undesired terms go here.",
        "cfg": "Sets the intensity of the prompt, 5 is common.",
        "seed": "Random number that generates the image, -1 for random.",
        "checkpoint": "The main AI model used to generate the image.",
        "vae": "The VAE converts the final details of the image.",
        "lora": "Shortcut to insert LoRA into the prompt.",
        "subseed": "Random number that defines variations on a set seed.",
        "variation": "Also known as subseed strength, makes variations on a set seed.",
    }

    _parameter_autocompletes = {
        "lora": loras_autocomplete,
        "checkpoint": checkpoint_autocomplete,
        "vae": vae_autocomplete,
    }


    @checks.bot_has_permissions(attach_files=True)
    @checks.bot_in_a_guild()
    @commands.command(name="txt2img")
    async def imagine(self, ctx: commands.Context, *, prompt: str):
        """
        Generate an image with Stable Diffusion

        **Arguments**
            - `prompt` a prompt to generate an image from
        """
        assert ctx.guild
        params = ImageGenParams(prompt=prompt)
        message_content=f"Result of {ctx.message.jump_url} requested by {ctx.author.mention}"
        await self.generate_image(ctx, params=params, message_content=message_content)


    @app_commands.command(name="txt2img")
    @app_commands.describe(resolution="The dimensions of the image.",
                           **_parameter_descriptions)
    @app_commands.autocomplete(**_parameter_autocompletes)
    @app_commands.checks.bot_has_permissions(attach_files=True)
    @app_commands.choices(resolution=[
            app_commands.Choice(name="Square", value="1024x1024"),
            app_commands.Choice(name="Portrait", value="832x1216"),
            app_commands.Choice(name="Landscape", value="1216x832"),
        ])
    @app_commands.guild_only()
    async def imagine_app(
        self,
        interaction: discord.Interaction,
        prompt: str,
        negative_prompt: str = None,
        resolution: str = "832x1216",
        checkpoint: str = None,
        lora: str = "",
        cfg: app_commands.Range[float, 2, 8] = None,
        seed: app_commands.Range[int, -1, None] = -1,
        subseed: app_commands.Range[int, -1, None] = -1,
        variation: app_commands.Range[float, 0.0, 0.5] = 0,
        vae: str = None,
    ):
        """
        Generate an image using Stable Diffusion.
        """
        await interaction.response.defer(thinking=True)

        ctx: commands.Context = await self.bot.get_context(interaction)  # noqa
        if not await self.can_run_command(ctx, "txt2img"):
            return await interaction.followup.send("You don't have permission to do this here.", ephemeral=True)

        width, height = tuple(int(x) for x in resolution.split("x"))

        params = ImageGenParams(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            cfg=cfg,
            seed=seed,
            checkpoint=checkpoint,
            vae=vae,
            lora=lora,
            subseed=subseed,
            subseed_strength=variation
        )

        await self.generate_image(interaction, params=params)


    @app_commands.command(name="img2img")
    @app_commands.describe(image="The input image.",
                           denoising="How much the image should change. Try around 0.6",
                           scale="Resizes the image up or down, 0.5 to 2.0.",
                           **_parameter_descriptions)
    @app_commands.autocomplete(**_parameter_autocompletes)
    @app_commands.checks.bot_has_permissions(attach_files=True)
    @app_commands.guild_only()
    async def reimagine_app(
            self,
            interaction: discord.Interaction,
            image: discord.Attachment,
            denoising: app_commands.Range[float, 0, 1],
            prompt: str,
            negative_prompt: str = None,
            checkpoint: str = None,
            lora: str = "",
            scale: app_commands.Range[float, 0.5, 2.0] = 1,
            cfg: app_commands.Range[float, 2, 8] = None,
            seed: app_commands.Range[int, -1, None] = -1,
            subseed: app_commands.Range[int, -1, None] = -1,
            variation: app_commands.Range[float, 0.0, 0.5] = 0,
            vae: str = None,
    ):
        """
        Convert an image using Stable Diffusion.
        """
        await interaction.response.defer(thinking=True)

        ctx: commands.Context = await self.bot.get_context(interaction)
        if not await self.can_run_command(ctx, "txt2img"):
            return await interaction.followup.send("You don't have permission to do this here.", ephemeral=True)

        assert ctx.guild and image.content_type
        if all(ext not in image.content_type for ext in SUPPORTED_IMAGE_TYPES):
            return await interaction.followup.send("The file you uploaded is not a valid image.", ephemeral=True)

        assert image.width and image.height
        size = image.width*image.height*scale*scale
        maxsize = (await self.config.max_img2img())**2
        if size > maxsize:
            return await interaction.followup.send(
                f"Max img2img size is {int(maxsize**0.5)}² pixels. "
                f"Your image {'after resizing would be' if scale != 0 else 'is'} {int(size**0.5)}² pixels, which is too big.",
                ephemeral=True)
        
        params = ImageGenParams(
            prompt=prompt,
            negative_prompt=negative_prompt,
            cfg=cfg,
            seed=seed,
            checkpoint=checkpoint,
            vae=vae,
            lora=lora,
            subseed=subseed,
            subseed_strength=variation,
            # img2img
            image=await image.read(),
            image_filename=image.filename,
            denoising=denoising,
            scale=scale,
            height=round(image.height*scale),
            width=round(image.width*scale),
        )

        await self.generate_image(interaction, params=params)


    @commands.command(name="autotag")
    async def autotag_cmd(self, ctx: commands.Context):
        """
        Generate booru tags for an image.
        """
        if not ctx.message.attachments:
            return await ctx.reply("You must use this command with an image.")

        image = ctx.message.attachments[0]
        assert ctx.guild and image.content_type
        if all(ext not in image.content_type for ext in SUPPORTED_IMAGE_TYPES):
            return await ctx.reply("The file you uploaded is not a valid image.")
        
        async with ctx.typing():
            await self.autotag(ctx, image)


    @app_commands.command(name="autotag")
    @app_commands.describe(image="The image to generate tags for")
    @app_commands.checks.bot_has_permissions(attach_files=True)
    @app_commands.guild_only()
    async def autotag_app(self, interaction: discord.Interaction, image: discord.Attachment):
        """
        Generate booru tags for an image.
        """
        ctx: commands.Context = await self.bot.get_context(interaction)  # noqa
        if not await self.can_run_command(ctx, "autotag"):
            return await interaction.followup.send("You don't have permission to do this here.", ephemeral=True)

        assert ctx.guild and image.content_type
        if all(ext not in image.content_type for ext in SUPPORTED_IMAGE_TYPES):
            return await interaction.followup.send("The file you uploaded is not a valid image.", ephemeral=True)
                
        await interaction.response.defer(thinking=True)
        await self.autotag(ctx, image)
        

    async def autotag(self, ctx: commands.Context, attachment: discord.Attachment):
        assert self.api
        image_bytes = await attachment.read()
        try:
            tags = await self.api.interrogate(image_bytes, attachment.filename)
        except aiohttp.ClientResponseError as error:
            log.exception("Autotagger")
            await ctx.reply(f":warning: Failed to tag the image! `{error.message}`")
        except Exception as error:
            log.exception("Autotagger")
            await ctx.reply(f":warning: Failed to tag the image! `{type(error).__name__}: {error}`")
        else:
            embed = discord.Embed(title="Autotagger Result", color=await self.bot.get_embed_color(ctx))
            if "sensitive" not in tags and "explicit" not in tags:
                embed.set_thumbnail(url=attachment.url)
            cleaned_tags = ', '.join([clean_tag(tag) for tag in tags if tag not in EXCLUDE_TAGGER])
            embed.description = f"`{cleaned_tags}`"
            await ctx.reply(embed=embed, allowed_mentions=discord.AllowedMentions.none())


    async def contains_blacklisted_word(self, prompt: str):
        blacklist_regex = await self.config.blacklist_regex()
        if blacklist_regex:
            return re.search(blacklist_regex, prompt, re.IGNORECASE)
        return False


    async def can_run_command(self, ctx: commands.Context, command_name: str) -> bool:
        prefix = await self.bot.get_prefix(ctx.message)
        prefix = prefix[0] if isinstance(prefix, list) else prefix
        fake_message = copy(ctx.message)
        fake_message.content = prefix + command_name
        command = ctx.bot.get_command(command_name)
        fake_context: commands.Context = await ctx.bot.get_context(fake_message)  # noqa
        try:
            can = await command.can_run(fake_context, check_all_parents=True, change_permission_state=False)
        except commands.CommandError:
            can = False
        return can

    @staticmethod
    def filter_names(options: dict, current: str, strict: bool = False) -> dict:
        results = {}
        ratios = [(item, fuzz.partial_ratio(current.lower(), item.lower())) for item in options.keys()]
        sorted_options = sorted(ratios, key=lambda x: x[1], reverse=True)
        for item, ratio in sorted_options:
            if strict and ratio < 75:
                continue
            results[item] = options[item]
        return results
