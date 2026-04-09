import os
import logging
import asyncio
import aiohttp
import discord
from io import BytesIO
from copy import deepcopy
from typing import Any, Coroutine
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
from redbot.core import commands

import aimage.constants as constants
from aimage.comfy import ComfyMetadata, ComfyMetadataReader
from aimage.utils import ImageGenError, build_split_masks, is_nsfw, send_response
from aimage.schema import ImageGenParams, QueuedImageGen
from aimage.commands import AImageCommands
from aimage.views.image_actions import ImageActions
from aimage.arcenciel_api import ArcEnCielAPI

log = logging.getLogger("red.holo-cogs.aimage")


class AImage(AImageCommands):
    """ Generate AI images using a A1111 endpoint """

    async def cog_load_when_ready(self):
        await self.bot.wait_until_red_ready()
        api_key = (await self.bot.get_shared_api_tokens("arcenciel")).get("api_key", "")
        self.api = ArcEnCielAPI(self, constants.ENDPOINT, api_key)
        asyncio.create_task(self.update_autocomplete_cache())
        self.consume_queue.start()
        self.clear_quota.start()
        self.resource_cache = await self.config.resource_cache()
    
    async def cog_load(self):
        asyncio.create_task(self.cog_load_when_ready())
        
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
        if not self.queued_images or not self.api.session:
            return
        if self.api.session.closed:
            error_message = f":warning: The generator restarted, please try again."
            for gen_id, gen in list(self.queued_images.items()):
                del self.queued_images[gen_id]
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
                if gen.id in self.queued_images:
                    del self.queued_images[gen.id]
                log.exception("Updating job")
                error_message = f"The bot aborted the operation due to an unexpected error.\n`{type(error).__name__}: {error}`"
                asyncio.create_task(self.finalize_image_generation(gen, False, error_message))


    async def update_job(self, job: dict[str, Any], gen: QueuedImageGen):
        assert isinstance(gen.context.channel, discord.abc.Messageable)
        now = datetime.now(timezone.utc)
        created = datetime.fromtimestamp(job["createdAt"] / 1000).astimezone(timezone.utc)

        if (now - created).total_seconds() > constants.JOB_TIMEOUT:
            del self.queued_images[gen.id]
            asyncio.create_task(self.finalize_image_generation(gen, False, "Timed out."))

        elif job["status"] in ["completed", "failed"]:
            del self.queued_images[gen.id]
            ratings = job.get("safety", {}).get("outputs", {}).values()
            nsfw = any(r.get("rating") in ["sensitive", "explicit"] for r in ratings)
            error_message = None
            if job["status"] == "failed":
                error_message = f"Reason: `{job.get('safety', {}).get('reason') or 'none'}`, " \
                              + f"Error: `{job.get('safety', {}).get('error') or 'none'}`"
            asyncio.create_task(self.finalize_image_generation(gen, nsfw, error_message))
            
        elif job["status"] in ["queued", "running"]:
            current_phase: str = job["progress"]["phase"]
            current_percent: int = job["progress"]["percent"]
            current_eta: int = job["progress"]["etaMs"] or job["queueEtaMs"] or 0
            current_position: int = job["position"]
            if (now - gen.last_updated).total_seconds() < constants.PROGRESS_UPDATE_INTERVAL:
                return
            if abs(gen.last_eta - current_eta) < 1000 and gen.last_percent == current_percent and gen.last_position == current_position:
                return
            gen.last_updated = now  
            gen.last_percent = current_percent
            gen.last_eta = current_eta
            gen.last_position = current_position
            
            embed = discord.Embed(color=await self.bot.get_embed_color(gen.context.channel))
            embed.description = f"{await self.config.loading_emoji()} "
            if current_phase == "queued":
                embed.description += "Image request received..."
                embed.add_field(name="Position in queue", value=f"`{current_position}`")
            elif current_phase == "upscaling":
                embed.description += "Upscaling image..."
                if current_percent >= 90:  # upscaling only goes from 90->100
                    current_percent = max(current_percent, 91) % 10 * 10
            elif current_phase == "finalizing":
                embed.description += "Finishing image..."
            else:
                embed.description += f"Generating image..."
            if current_percent > 0:
                embed.add_field(name="Progress", value=f"`{current_percent}%`")
            if current_eta > 1000:
                estimate = now + timedelta(milliseconds=current_eta)
                embed.add_field(name="ETA", value=f"<t:{int(estimate.timestamp())}:R>")
            elif current_percent > 0:
                embed.add_field(name="ETA", value="`soon`")

            if isinstance(gen.context, discord.Interaction):
                await gen.context.edit_original_response(embed=embed)
            elif gen.progress_message:
                await gen.progress_message.edit(embed=embed)


    async def generate_image(self,
                             context: commands.Context | discord.Interaction,
                             payload: dict | None = None,
                             params: ImageGenParams | None = None,
                             callback: Coroutine | None = None,
                             message_content: str | None = None):
        
        user = context.user if isinstance(context, discord.Interaction) else context.author
        channel = context.channel
        assert self.api and context.guild and isinstance(user, discord.Member) and isinstance(channel, discord.TextChannel | discord.Thread)
        assert payload or params
        payload = payload or await self.build_image_payload(params, user, is_nsfw(channel))  # type: ignore

        enabled = await self.config.guild(context.guild).enabled()
        if not enabled:
            return await send_response(context, content=":warning: The generator is not enabled for this server.")
        
        if await self.reject_non_vip(context):
            return

        prompt = params.prompt if params else payload.get("prompt", "")
        if await self.contains_blacklisted_word(prompt):
            return await send_response(context, content=":warning: Blocked prompt.")
        
        progress_message = None
        loading = await self.config.loading_emoji()
        embed = discord.Embed(description=f"{loading} Image request sent...")
        embed.color = await self.bot.get_embed_color(channel)
        if isinstance(context, commands.Context):
            progress_message = await context.reply(embed=embed, mention_author=False)
            if not callback:
                callback = progress_message.delete()
        else:
            await context.edit_original_response(embed=embed)
            
        try:
            if "masterpiece" not in prompt and "best quality" not in prompt:
                payload["prompt"] = "masterpiece, best quality, " + prompt
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
            self.queued_images[job["id"]] = QueuedImageGen(
                job["id"],
                payload,
                user,
                channel,
                context,
                callback,
                message_content,
                progress_message,
                datetime.now(timezone.utc),
            )
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
        tasks = [callback, send_response(context, content=error_message)]
        await asyncio.gather(*[t for t in tasks if t])


    async def finalize_image_generation(self, gen: QueuedImageGen, nsfw: bool, error_message: str | None):
        assert self.api and isinstance(gen.context, (commands.Context, discord.Interaction))

        if not self.api.closed:
            asyncio.create_task(self.api.close_request(gen.id))
        
        if error_message:
            content = f":warning: Failed to generate image. {error_message}"
            return await send_response(gen.context, content=content)
        
        final_tasks: list[Coroutine] = []
        try:
            image_bytes = await self.api.download_image(gen.id)
            metadata = ComfyMetadataReader.from_bytes(image_bytes)
            file_id = gen.context.id if isinstance(gen.context, discord.Interaction) else gen.context.message.id
            file = discord.File(BytesIO(image_bytes), filename=f"image_{file_id}.png", spoiler=nsfw)
            maxsize = await self.config.max_img2img()
            view = ImageActions(self, metadata, gen.payload, gen.user, gen.channel, maxsize)
            content = f"-# {gen.message_content}" if gen.message_content else None
            # send it
            message = await send_response(gen.context, file=file, view=view, content=content, allowed_mentions=discord.AllowedMentions.none())
            view.message = message
            self.gen_count[gen.user.id] += 1
            imagescanner = self.bot.get_cog("ImageScanner")
            if message and imagescanner and gen.channel.id in imagescanner.scan_channels:  # type: ignore
                imagescanner.image_cache[message.id] = ({0: metadata}, {0: image_bytes})  # type: ignore
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
            final_tasks.append(send_response(gen.context, content=error_message))
        if gen.callback:
            final_tasks.append(gen.callback)
            
        await asyncio.gather(*final_tasks)


    async def reject_non_vip(self, context: commands.Context | discord.Interaction) -> bool:
        user = context.user if isinstance(context, discord.Interaction) else context.author
        channel = context.channel
        assert context.guild and isinstance(user, discord.Member) and isinstance(channel, discord.abc.Messageable)

        vip_role = await self.config.guild(context.guild).vip_role()
        is_vip = await self.config.user(user).vip() or any(role.id == vip_role for role in user.roles)        
        quota = await self.config.quota()
        has_ongoing_gen = any(gen.user == user for gen in self.queued_images.values())
        elapsed_last_refresh = (datetime.now(timezone.utc) - self.last_quota_refresh).total_seconds()

        if is_vip:
            return False
        if has_ongoing_gen:
            content = "🕒 You must wait for your current image to finish generating before you can request a new one."
            await send_response(context, content=content, ephemeral=True)
            return True
        if self.gen_count[user.id] >= quota:
            if quota == 0:
                content = ":warning: You are not authorized to use the generator at this time. You may be interested in [<https://arcenciel.io>](our web generator)."
            else:
                content = "🕒 You have met your generation quota. You can wait for it to refresh, or try [<https://arcenciel.io>](our web generator)." \
                        + f"\n\nTime remaining: {int(60 - (elapsed_last_refresh // 60))} minutes."
            await send_response(context, content=content, ephemeral=True)
            return True
        return False


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
