import logging
import asyncio
import aiohttp
import discord
from io import BytesIO
from typing import Any, Coroutine
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
from redbot.core import commands
from sd_prompt_reader.image_data_reader import ImageDataReader

from aimage.utils import ImageGenError, delete_button_after, is_nsfw, send_response
from aimage.schema import ImageGenParams, QueuedImageGen
from aimage.commands import AImageCommands
from aimage.constants import ENDPOINT, JOB_TIMEOUT, PROGRESS_UPDATE_INTERVAL
from aimage.comfy import ComfyMetadataReader
from aimage.views.image_actions import ImageActions
from aimage.arcenciel_api import ArcEnCielAPI

log = logging.getLogger("red.holo-cogs.aimage")


class AImage(AImageCommands):
    """ Generate AI images using a A1111 endpoint """

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


    @tasks.loop(seconds=PROGRESS_UPDATE_INTERVAL/2, reconnect=True)
    async def consume_queue(self):
        assert self.api
        if not self.queued_images:
            return
        jobs = await self.api.fetch_queue()
        for job in jobs:
            gen = self.queued_images.get(job["id"])
            if not gen:
                continue
            try:
                await self.update_job(job, gen)
            except Exception as error:
                del self.queued_images[job["id"]]
                log.exception("Updating job")
                error_message = f"The bot aborted the operation due to an unexpected error.\n`{type(error).__name__}: {error}`"
                asyncio.create_task(self.finalize_image_generation(gen, False, error_message))


    async def update_job(self, job: dict[str, Any], gen: QueuedImageGen):
        assert isinstance(gen.context.channel, discord.abc.Messageable)
        now = datetime.now(timezone.utc)
        created = datetime.fromtimestamp(job["createdAt"] / 1000).astimezone(timezone.utc)
        log.info(f"job {gen.id} is running")

        if (now - created).total_seconds() > JOB_TIMEOUT:
            del self.queued_images[gen.id]
            asyncio.create_task(self.finalize_image_generation(gen, False, "Timed out."))

        elif job["status"] in ["completed", "failed"]:
            del self.queued_images[gen.id]
            rating = list(job["safety"]["outputs"].values())[0]["rating"] 
            nsfw = rating in ["sensitive", "explicit"]
            error_message = None
            if job["status"] == "failed":
                error_message = f"Reason: `{job['safety']['reason'] or 'none'}`, Error: `{job['safety']['error'] or 'none'}`"
            asyncio.create_task(self.finalize_image_generation(gen, nsfw, error_message))
            
        elif job["status"] in ["queued", "running"]:
            progress_phase: str = job['progress']['phase']
            progress_percent: int = job['progress']['percent']
            progress_eta: int | None = job['progress']['etaMs']
            log.info(f"{progress_phase} {progress_percent}% {progress_eta}ms")
            if (now - gen.last_updated).total_seconds() < PROGRESS_UPDATE_INTERVAL:
                return
            if gen.last_progress == progress_percent:
                return
            gen.last_updated = now          
            gen.last_progress = progress_percent
            log.info(f"Updating job {gen.id}")
            
            embed = discord.Embed(color=await self.bot.get_embed_color(gen.context.channel))
            embed.description = f"## {await self.config.loading_emoji()} "
            if progress_phase == "queued":
                embed.description += f"Image request in queue..."
            elif progress_phase == "upscaling":
                embed.description += f"Upscaling image..."
            else:
                embed.description += f"Generating image..."
            if progress_percent > 0:
                embed.add_field(name="Progress", value=f"{progress_percent}%")
            if progress_eta and progress_eta > 1000:
                estimate = now + timedelta(milliseconds=progress_eta)
                embed.add_field(name="ETA", value=f"<t:{int(estimate.timestamp())}:R>")

            if isinstance(gen.context, discord.Interaction):
                await gen.context.edit_original_response(embed=embed)
            elif gen.progress_message:
                await gen.progress_message.edit(embed=embed)


    async def generate_image(self,
                             context: commands.Context | discord.Interaction,
                             payload: dict = None,
                             params: ImageGenParams = None,
                             callback: Coroutine = None,
                             message_content: str = None):
        
        user = context.user if isinstance(context, discord.Interaction) else context.author
        channel = context.channel
        assert self.api and context.guild and isinstance(user, discord.Member) and isinstance(channel, discord.abc.Messageable)
        assert payload or params
        payload = payload or await self.api.build_image_payload(params, user, is_nsfw(channel))  # type: ignore

        enabled = await self.config.guild(context.guild).enabled()
        if not enabled:
            return await send_response(context, content=":warning: The generator is not enabled for this server.")
        
        if await self.reject_non_vip(context):
            return

        prompt = params.prompt if params else payload.get("prompt", "")

        if await self.contains_blacklisted_word(prompt):
            return await send_response(context, content=":warning: Blocked prompt.")

        current_content = ""
        progress_message = None
        loading = await self.config.loading_emoji()
        current_content = f"## {loading} Image request received..."
        embed = discord.Embed(description=current_content)
        embed.color = await self.bot.get_embed_color(channel)
        if isinstance(context, commands.Context):
            progress_message = await context.reply(embed=embed, mention_author=False)
            if not callback:
                callback = progress_message.delete()
        else:
            await context.edit_original_response(embed=embed)
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
                progress_message,
                datetime.now(timezone.utc),
            )
        except ImageGenError as error:
            content = f":warning: The image couldn't be generated. ({error})"
            asyncio.create_task(send_response(context, content=content))
        except aiohttp.ClientResponseError as error:
            content = f":warning: There was a problem generating the image! `{error.message}`"
            log.exception("Queueing image")
            asyncio.create_task(send_response(context, content=content))
        except Exception as error:
            content = f":warning: There was a problem generating the image! `{type(error).__name__}: {error}`"
            log.exception("Queueing image")
            asyncio.create_task(send_response(context, content=content))
        else:
            return
        if callback:
                await callback


    async def finalize_image_generation(self, gen: QueuedImageGen, nsfw: bool, error_message: str | None):
        assert self.api and isinstance(gen.context, (commands.Context, discord.Interaction))

        asyncio.create_task(self.api.close_request(gen.id))
        
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
        asyncio.create_task(delete_button_after(msg))
        imagescanner = self.bot.get_cog("ImageScanner")
        if imagescanner:
            if gen.channel.id in imagescanner.scan_channels:  # type: ignore
                imagescanner.image_cache[msg.id] = ({0: metadata_reader}, {0: image_bytes})  # type: ignore
                asyncio.create_task(msg.add_reaction("🔎"))


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
                content = ":warning: You are not authorized to use the generator at this time. You may be interested in the <https://arcenciel.io> generator."
            else:
                content = "🕒 You have met your generation quota. You can wait for it to refresh, or try the <https://arcenciel.io> generator." \
                        + f"\n\nTime remaining: {int(60 - (elapsed_last_refresh // 60))} minutes."
            await send_response(context, content=content, ephemeral=True)
            return True
        return False
