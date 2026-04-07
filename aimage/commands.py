import re
import logging
import asyncio
import aiohttp
import discord
from copy import copy

from redbot.core import app_commands, checks, commands

from aimage.utils import clean_tag, clean_model, edit_regional_prompts, filter_names, normalize_image
from aimage.schema import ImageGenParams, ImageRegionalParams, ImageToImageParams, SplitType
from aimage.settings import AImageSettings
from aimage.constants import EXCLUDE_TAGGER, SUPPORTED_IMAGE_TYPES, LORA_PATTERN, MAX_UPLOAD_PIXELS

log = logging.getLogger("red.holo-cogs.aimage")
 
class AImageCommands(AImageSettings):
    
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
        command = self.bot.get_command(command_name)
        assert command
        fake_context: commands.Context = await self.bot.get_context(fake_message)  # noqa
        try:
            can = await command.can_run(fake_context, check_all_parents=True, change_permission_state=False)
        except commands.CommandError:
            can = False
        return can

    async def build_autocomplete_choices(self, current: str, choices: dict) -> list[app_commands.Choice[str]]:
        if not choices:
            return []
        choices = filter_names(choices, current)
        return [app_commands.Choice(name=display_name.replace(".safetensors", ""), value=name.replace(".safetensors", ""))
                for display_name, name in choices.items()
                if len(name.replace(".safetensors", "")) <= 100
                ][:25]

    async def samplers_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        choices = self.autocomplete_cache.get("samplers") or {}
        return await self.build_autocomplete_choices(current, choices)

    async def checkpoint_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        choices = self.autocomplete_cache.get("checkpoints") or {}
        return await self.build_autocomplete_choices(current, choices)

    async def vae_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        choices = self.autocomplete_cache.get("vae") or {}
        return await self.build_autocomplete_choices(current, choices)
    
    async def loras_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if len(current) < 3:
            return []
        assert self.api
        results = await self.api.search_loras(current)
        return [app_commands.Choice(name=clean_model(name.replace(".safetensors", "")), value=name.replace(".safetensors", ""))
                for name in results
                if len(name.replace(".safetensors", "")) <= 100
                ][:25]
    

    _shared_parameter_descriptions = {
        "negative_prompt": "Undesired terms go here.",
        "cfg": "Sets the intensity of the prompt, 5 is common.",
        "seed": "Random number that generates the image, -1 for random.",
        "checkpoint": "The main AI model used to generate the image.",
        "vae": "The VAE converts the final details of the image.",
        "lora": "Shortcut to insert LoRA into the prompt.",
        "subseed": "Random number that defines variations on a set seed.",
        "variation": "Also known as subseed strength, makes variations on a set seed.",
    }

    _resolution_shorthands = {
        re.compile(r"(^|\s*,\s*)(vertical)(?=$|\s*,\s*)", re.IGNORECASE): (832, 1216),
        re.compile(r"(^|\s*,\s*)(horizontal)(?=$|\s*,\s*)", re.IGNORECASE): (1216, 832),
        re.compile(r"(^|\s*,\s*)(square)(?=$|\s*,\s*)", re.IGNORECASE): (1024, 1024),
        re.compile(r"(^|\s*,\s*)(ultrawide|widescreen)(?=$|\s*,\s*)", re.IGNORECASE): (1536, 640),
    }

    _resolution_choices = [
        app_commands.Choice(name="Square", value="1024x1024"),
        app_commands.Choice(name="Portrait", value="832x1216"),
        app_commands.Choice(name="Landscape", value="1216x832"),
        app_commands.Choice(name="Ultrawide", value="1536x640"),
    ]


    @checks.bot_has_permissions(attach_files=True)
    @checks.bot_in_a_guild()
    @commands.command(name="txt2img")
    async def imagine(self, ctx: commands.Context, *, prompt: str):
        """
        Generate an image with Stable Diffusion
        """
        assert ctx.guild

        width, height = None, None
        negative_prompt = None
        regions = None

        loras = []
        for lora, _, _ in LORA_PATTERN.findall(prompt):
            prompt = prompt.replace(lora, "").strip()
            loras.append(lora)

        if "--" in prompt:
            prompt, negative_prompt = [p.strip() for p in prompt.rsplit("--", 1)]

        for pattern, size in self._resolution_shorthands.items():
            if pattern.search(prompt):
                prompt = pattern.sub("", prompt).strip(" ,")
                width, height = size
                break
              
        if "||" in prompt:
            segments = [p.strip() for p in prompt.split("||")]
            if len(segments) != 3:
                content = f":warning: Your prompt contains regions divided by `||`, but it's not in the format `shared || left || right`"
                return await ctx.send(content)
            segments = edit_regional_prompts(*segments)
            prompt = segments[0]
            if width is None or height is None:
                width, height = 1216, 832
            regions = ImageRegionalParams(segments[1], segments[2], SplitType.HORIZONTAL.value, 50)

        params = ImageGenParams(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            regions=regions,
            loras=loras,
        )
        message_content=f"Result of {ctx.message.jump_url} requested by {ctx.author.mention}"
        await self.generate_image(ctx, params=params, message_content=message_content)


    @app_commands.command(name="txt2img")
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(attach_files=True, embed_links=True)
    @app_commands.describe(
        prompt="The prompt to generate an image from.",
        resolution="The dimensions of the image.",
        **_shared_parameter_descriptions,
    )
    @app_commands.autocomplete(
        lora=loras_autocomplete,
        vae=vae_autocomplete,
        checkpoint=checkpoint_autocomplete,
    )
    @app_commands.checks.bot_has_permissions(attach_files=True)
    @app_commands.choices(resolution=_resolution_choices)
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
        Generate an image using Stable Diffusion
        """
        await interaction.response.defer(thinking=True)

        ctx: commands.Context = await self.bot.get_context(interaction)  # noqa
        if not await self.can_run_command(ctx, "txt2img"):
            return await interaction.followup.send("You don't have permission to do this here.", ephemeral=True)

        width, height = tuple(int(x) for x in resolution.split("x"))

        loras = [lora] if lora else []
        for lora, _, _ in LORA_PATTERN.findall(prompt):
            prompt = prompt.replace(lora, "").strip()
            loras.append(lora)

        regions = None
        if "||" in prompt:
            segments = [p.strip() for p in prompt.split("||")]
            if len(segments) != 3:
                content = f":warning: Your prompt contains regions divided by `||`, but it's not in the format `shared || left || right`"
                return await interaction.followup.send(content=content, ephemeral=True)
            segments = edit_regional_prompts(*segments)
            prompt = segments[0]
            regions = ImageRegionalParams(segments[1], segments[2], SplitType.HORIZONTAL.value, 50)
            
        params = ImageGenParams(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            cfg=cfg,
            seed=seed,
            checkpoint=checkpoint,
            vae=vae,
            loras=loras,
            subseed=subseed,
            subseed_strength=variation,
            regions=regions,
        )

        await self.generate_image(interaction, params=params)


    @app_commands.command(name="txt2img-regions")
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(attach_files=True, embed_links=True)
    @app_commands.describe(
        shared_prompt="Prompt to put on all regions of the image.",
        prompt1="Prompt to put on the first region of the image.",
        prompt2="Prompt to put on the second region of the image.",
        lora2="Shortcut to insert LoRA into the prompt.",
        split="How to split the image",
        split_percent="Size of the first region, 50 to split the image in half.",
        resolution="The dimensions of the image.",
        **_shared_parameter_descriptions,
    )
    @app_commands.autocomplete(
        lora=loras_autocomplete,
        lora2=loras_autocomplete,
        vae=vae_autocomplete,
        checkpoint=checkpoint_autocomplete,
    )
    @app_commands.choices(
        resolution=_resolution_choices,
        split=[
            app_commands.Choice(name="Left/Right", value=SplitType.HORIZONTAL.value),
            app_commands.Choice(name="Top/Bottom", value=SplitType.VERTICAL.value),
        ],
    )
    async def regional_app(
        self,
        interaction: discord.Interaction,
        split: str,
        shared_prompt: str,
        prompt1: str,
        prompt2: str,
        split_percent: app_commands.Range[int, 10, 90] = 50,
        negative_prompt: str = None,
        resolution: str = "1024x1024",
        checkpoint: str = None,
        lora: str = "",
        lora2: str = "",
        cfg: app_commands.Range[float, 2, 8] = None,
        seed: app_commands.Range[int, -1, None] = -1,
        subseed: app_commands.Range[int, -1, None] = -1,
        variation: app_commands.Range[float, 0.0, 0.5] = 0,
        vae: str = None,
    ):
        """
        Generate an image with regional prompts using Stable Diffusion
        """
        await interaction.response.defer(thinking=True)

        ctx: commands.Context = await self.bot.get_context(interaction)  # noqa
        if not await self.can_run_command(ctx, "txt2img"):
            return await interaction.followup.send("You don't have permission to do this here.", ephemeral=True)

        width, height = tuple(int(x) for x in resolution.split("x"))
        final_prompt, prompt1, prompt2 = edit_regional_prompts(shared_prompt, prompt1, prompt2)
        regions = ImageRegionalParams(prompt1, prompt2, split, split_percent)

        params = ImageGenParams(
            prompt=final_prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            cfg=cfg,
            seed=seed,
            checkpoint=checkpoint,
            vae=vae,
            loras=[l for l in (lora, lora2) if l],
            subseed=subseed,
            subseed_strength=variation,
            regions=regions,
        )

        await self.generate_image(interaction, params=params)


    @app_commands.command(name="img2img")
    @app_commands.describe(
        image="The input image.",
        denoising="How much the image should change. Try around 0.6",
        scale="Resizes the image up or down, 0.5 to 2.0.",
        **_shared_parameter_descriptions,
    )
    @app_commands.autocomplete(
        lora=loras_autocomplete,
        vae=vae_autocomplete,
        checkpoint=checkpoint_autocomplete,
    )
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
        Convert an image using Stable Diffusion
        """
        await interaction.response.defer(thinking=True)

        ctx: commands.Context = await self.bot.get_context(interaction)
        if not await self.can_run_command(ctx, "txt2img"):
            return await interaction.followup.send("You don't have permission to do this here.", ephemeral=True)

        assert ctx.guild and image.content_type
        if all(ext not in image.content_type for ext in SUPPORTED_IMAGE_TYPES):
            return await interaction.followup.send("The file you uploaded is not a valid image.", ephemeral=True)

        assert image.width and image.height
        maxsize = (await self.config.max_img2img())**2
        size = image.width*image.height*scale*scale
        if size > maxsize:
            return await interaction.followup.send(
                f"Max img2img size is {int(maxsize**0.5)}² pixels. "
                f"Your image {'after resizing would be' if scale != 0 else 'is'} {int(size**0.5)}² pixels, which is too big.",
                ephemeral=True)

        image_bytes = await asyncio.to_thread(normalize_image, await image.read(), maxsize)
        image_name = image.filename.rsplit(".", 1)[0] + ".png"
        img2img_params = ImageToImageParams(image_bytes, image_name, denoising, scale)

        params = ImageGenParams(
            prompt=prompt,
            negative_prompt=negative_prompt,
            cfg=cfg,
            seed=seed,
            checkpoint=checkpoint,
            vae=vae,
            loras=[lora] if lora else [],
            subseed=subseed,
            subseed_strength=variation,
            height=round(image.height*scale),
            width=round(image.width*scale),
            image=img2img_params,
        )

        await self.generate_image(interaction, params=params)


    @commands.command(name="autotag")
    async def autotag_cmd(self, ctx: commands.Context):
        """
        Generate booru tags for an image
        """
        attachments = ctx.message.attachments
        if not attachments:
            if ctx.message.reference and ctx.message.reference.message_id:
                reference_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                attachments = reference_message.attachments
            if not attachments:
                return await ctx.reply("You must use this command with an image.")

        image = attachments[0]
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
        Generate booru tags for an image
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
        image_bytes = await asyncio.to_thread(normalize_image, await attachment.read(), MAX_UPLOAD_PIXELS)
        image_name = attachment.filename.rsplit(".", 1)[0] + ".png"
        try:
            tags = await self.api.interrogate(image_bytes, image_name)
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
