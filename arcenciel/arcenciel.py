import os
import logging
import asyncio
import base64
import binascii
import aiohttp
import discord
import humanize
from io import BytesIO
from copy import deepcopy
from typing import Any, Coroutine
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
from redbot.core import commands

from arcenciel import constants
from arcenciel.comfy import ComfyMetadata, ComfyMetadataReader
from arcenciel.utils import ImageGenError, build_split_masks, is_nsfw, send_response, gather_raise_all
from arcenciel.schema import ImageGenParams, QueuedImageGen
from arcenciel.commands import ArcencielCommands
from arcenciel.views.generating import GeneratingView, GeneratingV2View
from arcenciel.views.image_actions import ImageActions
from arcenciel.arcenciel_api import ArcEnCielAPI

log = logging.getLogger("red.holo-cogs.arcenciel")


class Arcenciel(ArcencielCommands):
    """Generate AI images with arcenciel.io"""

    async def cog_load_when_ready(self):
        await self.bot.wait_until_red_ready()
        api_key = (await self.bot.get_shared_api_tokens("arcenciel")).get("api_key", "")
        self.api = ArcEnCielAPI(self, constants.ENDPOINT, api_key)
        asyncio.create_task(self.update_autocomplete_cache())
        self.consume_queue.start()
        self.preview_task = asyncio.create_task(self.consume_previews())
        self.resource_cache = await self.config.resource_cache()
    
    async def cog_load(self):
        asyncio.create_task(self.cog_load_when_ready())
        
    async def cog_unload(self):
        self.consume_queue.stop()
        preview_task = getattr(self, "preview_task", None)
        if preview_task:
            preview_task.cancel()
            await asyncio.gather(preview_task, return_exceptions=True)
        if self.api:
            await self.api.session.close()

    async def update_autocomplete_cache(self):
        assert self.api
        return await self.api.update_autocomplete_cache()

    async def consume_previews(self):
        assert self.api
        retry_delay = 2
        while not self.api.session.closed:
            try:
                async for preview in self.api.stream_previews():
                    retry_delay = 2
                    gen = self.queued_images.get(preview.get("jobId"))
                    if (not gen or gen.cancelled or gen.preview_disabled or not gen.progress_v2_view
                            or not is_nsfw(gen.channel)):
                        continue
                    mime_type = preview.get("mimeType")
                    step = preview.get("step")
                    total_steps = preview.get("totalSteps")
                    encoded = preview.get("imageBase64")
                    if (mime_type not in ("image/jpeg", "image/png") or type(step) is not int
                            or type(total_steps) is not int or step < 1 or total_steps < step
                            or not isinstance(encoded, str) or len(encoded) > 700_000):
                        continue
                    try:
                        image = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError):
                        continue
                    if not image or len(image) > 512 * 1024:
                        continue
                    gen.pending_preview = (image, mime_type, step, total_steps)
                    gen.preview_version += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Generator preview stream disconnected", exc_info=True)
            if self.api.session.closed:
                return
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

    @tasks.loop(seconds=1.5, reconnect=True)
    async def consume_queue(self):
        assert self.api
        if not self.queued_images or not self.api.session:
            return
        if self.api.session.closed:
            error_message = f":warning: The generator restarted, please try again."
            for gen_id, gen in list(self.queued_images.items()):
                self.queued_images.pop(gen_id, None)
                asyncio.create_task(self.finalize_image_generation(gen, False, error_message))
            return
        jobs = await self.api.fetch_queue()
        for job in jobs:
            gen = self.queued_images.get(job["id"])
            if not gen:
                continue
            try:
                await self.update_job(job, gen)
            except Exception as error:
                self.queued_images.pop(gen.id, None)
                log.exception("Updating job")
                error_message = f"The bot aborted the operation due to an unexpected error.\n`{type(error).__name__}: {error}`"
                asyncio.create_task(self.finalize_image_generation(gen, False, error_message))


    async def update_job(self, job: dict[str, Any], gen: QueuedImageGen):
        assert isinstance(gen.context.channel, discord.abc.Messageable)
        now = datetime.now(timezone.utc)
        created = datetime.fromtimestamp(job["createdAt"] / 1000).astimezone(timezone.utc)
        user = gen.context.author if isinstance(gen.context, commands.Context) else gen.context.user
        assert isinstance(user, discord.Member)

        if (now - created).total_seconds() > constants.JOB_TIMEOUT:
            self.queued_images.pop(gen.id, None)
            gen.pending_preview = None
            asyncio.create_task(self.finalize_image_generation(gen, False, "Timed out."))

        elif job["status"] in ["completed", "failed"]:
            self.queued_images.pop(gen.id, None)
            gen.pending_preview = None
            ratings = job.get("safety", {}).get("outputs", {}).values()
            nsfw = any(r.get("rating") in ["sensitive", "explicit"] for r in ratings)
            error_message = None
            if job["status"] == "failed":
                error_message = job.get("error") or job.get("safety", {}).get("reason") or "Unknown error."
            asyncio.create_task(self.finalize_image_generation(gen, nsfw, error_message))
            
        elif job["status"] in ["queued", "running"]:
            current_phase: str = job["progress"]["phase"]
            current_percent: int = job["progress"]["percent"]
            current_eta: int = job["progress"]["etaMs"] or job["queueEtaMs"] or 0
            current_position: int = job["position"]
            show_preview = gen.progress_v2_view is not None and is_nsfw(gen.channel)
            preview_version = gen.preview_version
            pending_preview = (gen.pending_preview if show_preview and not gen.preview_disabled
                               and preview_version != gen.displayed_preview_version else None)
            clear_preview = gen.progress_v2_view is not None and not show_preview and gen.preview_filename is not None
            if (now - gen.last_updated).total_seconds() < constants.PROGRESS_UPDATE_INTERVAL:
                return
            if (abs(gen.last_eta - current_eta) < 1000 and gen.last_percent == current_percent
                    and gen.last_position == current_position and not pending_preview and not clear_preview):
                return
            
            phase_text = "Generating image..."
            if current_phase == "queued":
                phase_text = "Image request received..."
            elif current_phase == "upscaling":
                phase_text = "Upscaling image..."
            elif current_phase == "refining":
                phase_text = "Refining image..."
            elif current_phase == "warmup":
                phase_text = "Preparing generator..."
            elif current_phase == "finalizing":
                phase_text = "Finishing image..."
            status = f"{await self.config.loading_emoji()} {phase_text}"
            if current_phase == "queued" and gen.progress_v2_view:
                status += f"  ·  Position in queue `{current_position}`"
            progress_value = f"`{current_percent}%`" if current_percent > 0 else None
            eta_value = None
            if current_eta > 1000:
                estimate = now + timedelta(milliseconds=current_eta)
                eta_value = f"<t:{int(estimate.timestamp())}:R>"
            elif current_percent > 0:
                eta_value = "`soon`"

            preview_filename = gen.preview_filename
            preview_step = gen.preview_step
            preview_total_steps = gen.preview_total_steps
            preview_file = None
            if pending_preview:
                image, mime_type, preview_step, preview_total_steps = pending_preview
                extension = "jpg" if mime_type == "image/jpeg" else "png"
                preview_file = discord.File(
                    BytesIO(image), filename=f"preview_{gen.id}_{preview_version}.{extension}", spoiler=True
                )
                preview_filename = preview_file.filename
            step_value = f"`{preview_step}/{preview_total_steps}`" if show_preview and preview_filename else None

            if gen.progress_v2_view:
                gen.progress_v2_view.set_progress(status, progress_value, eta_value, step_value,
                                                  preview_filename if show_preview else None)
                edit_kwargs = {"view": gen.progress_v2_view, "allowed_mentions": discord.AllowedMentions.none()}
            else:
                embed = discord.Embed(color=await self.bot.get_embed_color(gen.context.channel))
                embed.description = status
                embed.set_footer(text=user.display_name, icon_url=user.display_avatar.url)
                if current_phase == "queued":
                    embed.add_field(name="Position in queue", value=f"`{current_position}`")
                if progress_value:
                    embed.add_field(name="Progress", value=progress_value)
                if eta_value:
                    embed.add_field(name="ETA", value=eta_value)
                edit_kwargs = {"embed": embed}

            async def edit_progress(attachments=None):
                kwargs = dict(edit_kwargs)
                if attachments is not None:
                    kwargs["attachments"] = attachments
                if isinstance(gen.context, discord.Interaction):
                    await gen.context.edit_original_response(**kwargs)
                elif gen.progress_message:
                    await gen.progress_message.edit(**kwargs)

            if pending_preview:
                try:
                    await edit_progress([preview_file])
                except discord.HTTPException:
                    log.warning("Unable to upload generator preview for job %s", gen.id, exc_info=True)
                    gen.preview_disabled = True
                    gen.pending_preview = None
                    old_step = f"`{gen.preview_step}/{gen.preview_total_steps}`" if gen.preview_filename else None
                    gen.progress_v2_view.set_progress(status, progress_value, eta_value, old_step, gen.preview_filename)
                    await edit_progress()
                else:
                    gen.preview_filename = preview_filename
                    gen.preview_step = preview_step
                    gen.preview_total_steps = preview_total_steps
                    gen.displayed_preview_version = preview_version
                    if gen.preview_version == preview_version:
                        gen.pending_preview = None
            else:
                await edit_progress([] if clear_preview else None)
                if clear_preview:
                    gen.preview_filename = None
                    gen.preview_step = 0
                    gen.preview_total_steps = 0
                    gen.pending_preview = None

            gen.last_updated = datetime.now(timezone.utc)
            gen.last_percent = current_percent
            gen.last_eta = current_eta
            gen.last_position = current_position


    async def generate_image(self,
                             context: commands.Context | discord.Interaction,
                             payload: dict | None = None,
                             params: ImageGenParams | None = None,
                             callback: Coroutine | None = None,
                             message_content: str | None = None
                            ):
        user = context.user if isinstance(context, discord.Interaction) else context.author
        channel = context.channel
        assert self.api and context.guild and isinstance(user, discord.Member) and isinstance(channel, discord.TextChannel | discord.Thread)
        assert payload or params
        payload = payload or await self.build_image_payload(params, user, is_nsfw(channel))  # type: ignore

        enabled = await self.config.guild(context.guild).enabled()
        if not enabled:
            return await send_response(context, content=":warning: The generator is not enabled for this server.")
        
        if not await self.check_quota(context):
            return

        prompt = params.prompt if params else payload.get("prompt", "")
        if await self.contains_blacklisted_word(prompt):
            return await send_response(context, content=":warning: Blocked prompt.")
        
        gen = QueuedImageGen(
            "", payload,
            user, channel, context,
            callback, message_content, None,
            datetime.now(timezone.utc),
        )
        loading = await self.config.loading_emoji()
        color = await self.bot.get_embed_color(channel)
        if is_nsfw(channel):
            view = GeneratingV2View(self, gen, color)
            gen.progress_v2_view = view
            view.set_progress(f"{loading} Image request sent...")
        else:
            view = GeneratingView(self, gen)
            embed = discord.Embed(description=f"{loading} Image request sent...", color=color)
            embed.set_footer(text=user.display_name, icon_url=user.display_avatar.url)
        if isinstance(context, commands.Context):
            if gen.progress_v2_view:
                gen.progress_message = await context.reply(view=view, mention_author=False,
                                                           allowed_mentions=discord.AllowedMentions.none())
            else:
                gen.progress_message = await context.reply(embed=embed, view=view, mention_author=False)
            gen.callback = gather_raise_all(callback, gen.progress_message.delete())
        else:
            if gen.progress_v2_view:
                await context.edit_original_response(embed=None, content=None, attachments=[], view=view,
                                                     allowed_mentions=discord.AllowedMentions.none())
            else:
                await context.edit_original_response(embed=embed, view=view)
    
        try:
            if params and params.image:
                path = await self.api.upload_image(params.image.data, params.image.filename or "image.png")
                payload["imagePath"] = path
            if params and params.regions and payload.get("attentionCouple"):
                mask_paths = []
                masks = build_split_masks(payload["width"], payload["height"], params.regions.split_percent, params.regions.split_type)
                for filename, data in masks:
                    mask_paths.append(await self.api.upload_image(data, filename or "image.png"))
                for i, path in enumerate(mask_paths):
                    payload["attentionCouple"]["regions"][i]["maskPath"] = path
                
            job = await self.api.request_image(payload)
            if gen.cancelled:  # gotta love race conditions
                await self.api.cancel_request(job["id"])
            else:
                gen.id = job["id"]
                self.queued_images[job["id"]] = gen

        except ImageGenError as error:
            error_message = f":warning: The image couldn't be generated. ({error})"
        except (aiohttp.ContentTypeError, aiohttp.ClientConnectionError) as error:
            error_message = f":warning: The image couldn't be generated. ({error})"
            log.warning("Queueing image", f"{type(error).__name__}: {error}")
        except aiohttp.ClientResponseError as error:
            error_message = f":warning: There was a problem generating the image! `{error.message}`"
            log.exception("Queueing image")
        except Exception as error:
            error_message = f":warning: There was a problem generating the image! `{type(error).__name__}: {error}`"
            log.exception("Queueing image")
        else:
            return
        # After exception
        await gather_raise_all(gen.callback, self.send_generation_response(gen, content=error_message))

    async def send_generation_response(self, gen: QueuedImageGen, **kwargs):
        if gen.progress_v2_view and isinstance(gen.context, discord.Interaction):
            message = await gen.context.followup.send(**kwargs)
            try:
                await gen.context.delete_original_response()
            except discord.HTTPException:
                log.warning("Unable to remove completed generator progress for job %s", gen.id, exc_info=True)
            try:
                return await gen.channel.fetch_message(message.id)
            except discord.HTTPException:
                return message
        return await send_response(gen.context, **kwargs)


    async def finalize_image_generation(self, gen: QueuedImageGen, nsfw: bool, error_message: str | None):
        assert self.api and isinstance(gen.context, (commands.Context, discord.Interaction))

        if not self.api.session.closed:
            asyncio.create_task(self.api.close_request(gen.id))
        
        if error_message:
            content = f":warning: Failed to generate image. {error_message}"
            return await gather_raise_all(gen.callback, self.send_generation_response(gen, content=content))
        
        final_tasks = [gen.callback]
        try:
            image_bytes = await self.api.download_image(gen.id)
            metadata = ComfyMetadataReader.from_bytes(image_bytes)
            file_id = gen.context.id if isinstance(gen.context, discord.Interaction) else gen.context.message.id
            file = discord.File(BytesIO(image_bytes), filename=f"image_{file_id}.png", spoiler=nsfw)
            maxsize = await self.config.max_img2img()
            view = ImageActions(self, metadata, gen.payload, gen.user, gen.channel, maxsize)
            content = f"-# {gen.message_content}" if gen.message_content else None
            # send it
            message = await self.send_generation_response(gen, file=file, view=view, content=content,
                                                          allowed_mentions=discord.AllowedMentions.none())
            view.message = message
            quota_progress = self.config.user(gen.user).quota_progress
            await quota_progress.set(await quota_progress() + 1)
            imagescanner = self.bot.get_cog("ImageScanner")
            if message and imagescanner and gen.channel.id in getattr(imagescanner, "scan_channels"):
                getattr(imagescanner, "image_cache")[message.id] = ({0: metadata}, {0: image_bytes})
                final_tasks.append(message.add_reaction("🔎"))
        except ImageGenError as error:
            error_message = f":warning: Failed to retrieve image. ({error})"
        except (aiohttp.ContentTypeError, aiohttp.ClientConnectionError) as error:
            error_message = f":warning: Failed to retrieve image! Service is down temporarily."
            log.warning(f"Finalizing image", f"{type(error).__name__}: {error}")
        except aiohttp.ClientResponseError as error:
            error_message = f":warning: Failed to retrieve image! `{error.message}`"
            log.exception("Finalizing image")
        except Exception as error:
            error_message = f":warning: Failed to retrieve image! `{type(error).__name__}: {error}`"
            log.exception("Finalizing image")

        if error_message:
            final_tasks.append(self.send_generation_response(gen, content=error_message))
            
        await gather_raise_all(*final_tasks)


    async def check_quota(self, context: commands.Context | discord.Interaction) -> bool:
        user = context.user if isinstance(context, discord.Interaction) else context.author
        channel = context.channel
        assert context.guild and isinstance(user, discord.Member) and isinstance(channel, discord.abc.Messageable)

        role_configs = await self.config.all_roles()
        role_quotas: dict[int, int] = {key: config["quota"] for key, config in role_configs.items()}
        quota_start = datetime.fromisoformat(await self.config.user(user).quota_start())
        quota_progress: int = await self.config.user(user).quota_progress()
        has_ongoing_gen = any(gen.user == user for gen in self.queued_images.values())
        now = datetime.now(timezone.utc)
        quota_elapsed = (now - quota_start).total_seconds()
        role_ids = [role.id for role in user.roles]
        highest_quota = max([role_quotas.get(rid, 0) for rid in role_ids], default=0)

        if quota_elapsed > constants.QUOTA_PERIOD:
            quota_progress, quota_elapsed = 0, 0
            await self.config.user(user).quota_start.set(now.isoformat())
            await self.config.user(user).quota_progress.set(0)

        embed = discord.Embed(color=await self.bot.get_embed_color(channel))
        embed.set_footer(text=user.display_name, icon_url=user.display_avatar.url)
        if has_ongoing_gen and highest_quota < constants.QUOTA_VIP_THRESHOLD:
            embed.description = "🕒 You must wait for your current image to finish generating before you can request a new one."
            await send_response(context, embed=embed, ephemeral=True)
            return False
        if highest_quota <= 0:
            embed.description = ":warning: You are not authorized to use the generator at this time. You may be interested in [our web generator](<https://arcenciel.io/generate>)."
            await send_response(context, embed=embed, ephemeral=True)
            return False
        if quota_progress >= highest_quota:
            embed.description = "🕒 You have met your generation quota. You can wait for it to refresh, or try [our web generator](<https://arcenciel.io/generate>)."
            remaining = constants.QUOTA_PERIOD - quota_elapsed
            remaining_str = humanize.precisedelta(remaining, suppress=["seconds"] if remaining > 3600 else [], format="%02d")
            embed.add_field(name="Time remaining", value=remaining_str)
            await send_response(context, embed=embed, ephemeral=True)
            return False
        return True


    async def resolve_arcenciel_resources(self, metadata: ComfyMetadata) -> list[str]:
        assert self.api
        hyperlinks: set[str] = set()
        hints = metadata.resource_hint_strings()
        files = [str(os.path.basename(filename.strip(' "'))) for filename in constants.RESOURCE_FILE_PATTERN.findall(metadata.raw or "")]
        for hint in set(hints + files):
            if hint not in self.resource_cache and hint in self.resource_not_found_cache:
                continue
            if hint in self.resource_cache:
                hyperlinks.add(self.resource_cache[hint])
                continue
            is_hash = constants.RESOURCE_HASH_PATTERN.match(hint) is not None
            resources = await self.api.search_resource(hint, hash_only=is_hash)
            log.info(f"Resource matches for {hint} /// " + ", ".join([str(model["id"]) for model in resources]))
            if not resources:
                await self.cache_set(hint, None)
                continue
            if is_hash or len(resources) == 1:
                choice = resources[0]
            else:
                choice = None
                for model in resources:
                    version_names = []
                    for version in model["versions"]:
                        vns = [version.get("fileName"), version.get("filePath"), version.get("originalName")]
                        version_names += [vn for vn in vns if vn]
                    if any(hint in name for name in version_names):
                        choice = model
                        break
            if choice:
                link = f"`{choice['type']}` [{choice['title']}](https://arcenciel.io/models/{choice['id']})"
                await self.cache_set(hint, link)
                hyperlinks.add(link)
        return sorted(list(hyperlinks))


    async def build_image_payload(self, params: ImageGenParams, member: discord.Member, nsfw: bool) -> dict:
        stock_negative_prompt = await self.config.negative_prompt()
        if stock_negative_prompt not in (params.negative_prompt or ""):
            if params.negative_prompt:
                params.negative_prompt = f"{stock_negative_prompt}, {params.negative_prompt}"
            else:
                params.negative_prompt = stock_negative_prompt
        
        checkpoint = params.checkpoint or await self.config.user(member).checkpoint() or await self.config.checkpoint() or ""
        vae = params.vae or await self.config.vae()
        loras = []
        for lora in params.loras:
            if m := constants.LORA_PATTERN.match(lora):
                name, weight = m.group(2), m.group(3)
            else:
                name, weight = lora, 1.0
            filename = name.replace(".safetensors", "") + ".safetensors"
            loras.append({ "name": filename, "weight": weight })

        payload = {
            "mode": "img2img" if params.image else "txt2img",
            "prompt": params.prompt,
            "negativePrompt": params.negative_prompt or await self.config.negative_prompt(),
            "modelName": checkpoint.replace(".safetensors", "") + ".safetensors",
            "vaeName": vae.replace(".safetensors", "") + ".safetensors" if vae else None,
            "seed": params.seed,
            "steps": params.steps or await self.config.sampling_steps(),
            "cfg": float(params.cfg or await self.config.cfg()),
            "samplerName": params.sampler or await self.config.sampler(),
            "scheduler": params.scheduler or await self.config.scheduler(),
            "width": params.width or await self.config.width(),
            "height": params.height or await self.config.height(),
            "batchSize": 1,
            "extraSeed": params.subseed,
            "extraSeedStrength": float(params.subseed_strength),
            "loras": loras,
            "sfwMode": not nsfw,
        }

        if params.image:
            if params.image.denoising is not None:
                payload["denoise"] = params.image.denoising
            if params.image.scale is not None:
                payload["scaleFactor"] = params.image.scale

        if params.regions:
            payload["attentionCouple"] = {
                "enabled": True,
                "layoutPreset": params.regions.split_type,
                "splitPercent": params.regions.split_percent,
                "globalPromptWeight": 0.3,
                "regions": [
                    {"prompt": params.regions.prompt1, "weight": 1, "maskPath": None,},
                    {"prompt": params.regions.prompt2, "weight": 1, "maskPath": None,},
                ],
            }

        if await self.config.adetailer():
            payload["adetailer"] = deepcopy(constants.ADETAILER_ARGS)
        
        return payload
