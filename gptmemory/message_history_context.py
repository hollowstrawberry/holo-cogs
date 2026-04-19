import logging
import asyncio
import aiohttp
import discord
import tiktoken
import xmltodict
from io import BytesIO
from typing import Any, Awaitable, Callable
from expiringdict import ExpiringDict
from redbot.core import commands
from redbot.core.bot import Red, Config

from gptmemory import utils as utils
from gptmemory import constants as constants
from gptmemory.schema import GptImageContent, GptMemoryResult, GptMessage, ParsedMessageResult, StructuredObject
from gptmemory.schema import DiscordMessageImageCandidates, DiscordMessageResolvedImages, ImageSource


log = logging.getLogger("gptmemory.context")


class ContextBuilder:
    def __init__(
        self,
        bot: Red,
        config: Config,
        session: aiohttp.ClientSession,
        encoding: tiktoken.Encoding,
        execute_captioner: Callable[[GptImageContent], Awaitable[str]],
        is_busy: Callable[[int], bool],
    ):
        self.bot = bot
        self.config = config
        self.session = session
        self.encoding = encoding
        self.execute_captioner = execute_captioner
        self.is_busy = is_busy
        self.attachment_image_cache: dict[int, tuple[int, bytes]]  = ExpiringDict(max_len=25, max_age_seconds=24*60*60)
        self.url_image_cache: dict[str, bytes]                     = ExpiringDict(max_len=25, max_age_seconds=24*60*60)
        self.attachment_caption_cache: dict[int, tuple[int, str]]  = ExpiringDict(max_len=200, max_age_seconds=24*60*60)
        self.url_caption_cache: dict[str, str]                     = ExpiringDict(max_len=200, max_age_seconds=24*60*60)


    async def build_message_history_context(
        self,
        ctx: commands.Context,
        backread: list[discord.Message],
        result: GptMemoryResult,
    ) -> list[GptMessage]:
        assert ctx.guild and self.bot.user

        max_image_size      = await self.config.guild(ctx.guild).max_image_resolution()
        max_images          = await self.config.guild(ctx.guild).max_images()
        max_quote_length    = await self.config.guild(ctx.guild).max_quote()
        max_file_length     = await self.config.guild(ctx.guild).max_text_file()
        max_backread_tokens = await self.config.guild(ctx.guild).backread_tokens()

        # Pass 1: decide which images will be downloaded and which will be captioned

        all_candidates: dict[int, DiscordMessageImageCandidates] = {}
        quotes_needed: dict[int, int | None] = {}

        images_remaining = max_images

        for n, backmsg in enumerate(backread):
            ref = backmsg.reference
            if ref and not (len(backread) > n + 1 and ref.message_id == backread[n + 1].id):  # prevent consecutive quote chains
                quotes_needed[backmsg.id] = ref.message_id
            else:
                quotes_needed[backmsg.id] = None

            candidate_attachments: list[tuple[int, discord.Attachment]] = [
                (i, att) for i, att in enumerate(backmsg.attachments)
                if att.content_type and att.content_type.startswith("image/")
            ]
            candidate_urls: list[str] = []
            if not candidate_attachments:
                for embed in backmsg.embeds:
                    if embed.image and embed.image.url:
                        candidate_urls.append(embed.image.url)
                    if embed.thumbnail and embed.thumbnail.url:
                        candidate_urls.append(embed.thumbnail.url)
                for match in constants.URL_PATTERN.findall(backmsg.content or ""):
                    if match.endswith(constants.IMAGE_EXTENSIONS):
                        candidate_urls.append(match)

            current_candidates: list[tuple[int, discord.Attachment] | str] = candidate_attachments + candidate_urls
            download_slots = max(0, images_remaining)
            download_list  = current_candidates[:download_slots]
            caption_list   = current_candidates[download_slots:]
            images_remaining -= len(download_list)
            all_candidates[backmsg.id] = DiscordMessageImageCandidates(backmsg.id, download_list, caption_list)

        # Pass 2: build coroutines to be executed concurrently

        async def resolve_quote(backmsg: discord.Message) -> tuple[int, discord.Message | None]:
            mid = quotes_needed[backmsg.id]
            if mid is None:
                return backmsg.id, None
            if backmsg.reference and backmsg.reference.cached_message:
                return backmsg.id, backmsg.reference.cached_message
            try:
                return backmsg.id, await backmsg.channel.fetch_message(mid)
            except discord.DiscordException:
                return backmsg.id, None

        async def resolve_images(backmsg: discord.Message) -> DiscordMessageResolvedImages:
            candidates = all_candidates[backmsg.id]
            all_srcs = (candidates.download + candidates.caption)[:constants.MAX_IMAGES_PER_MESSAGE]
            download_srcs = [s for s in all_srcs if s in candidates.download]
            caption_srcs  = [s for s in all_srcs if s in candidates.caption]

            async def process_download(src: ImageSource) -> tuple[ImageSource, bytes] | None:
                # check cache
                if isinstance(src, tuple):
                    cached = self.attachment_image_cache.get(backmsg.id)
                    if cached is not None:
                        return src, cached[1]
                else:
                    cached = self.url_image_cache.get(src)
                    if cached is not None:
                        return src, cached
                # not cached
                data = await self.fetch_and_normalize(backmsg, src, max_image_size)
                if data is None:
                    return None
                if isinstance(src, tuple):
                    self.attachment_image_cache[backmsg.id] = (src[0], data)
                else:
                    self.url_image_cache[src] = data
                return src, data

            async def process_caption(src: ImageSource) -> tuple[ImageSource, str] | None:
                # check cache
                if isinstance(src, tuple):
                    cached = self.attachment_caption_cache.get(backmsg.id)
                    if cached is not None:
                        return src, cached[1]
                else:
                    cached = self.url_caption_cache.get(src)
                    if cached is not None:
                        return src, cached
                # not cached
                data = await self.fetch_and_normalize(backmsg, src, max_image_size)
                if data is None:
                    return None
                image_content = utils.make_image_content(data)
                caption = await self.execute_captioner(image_content)
                if caption is None:
                    return None
                if isinstance(src, tuple):
                    self.attachment_caption_cache[backmsg.id] = (src[0], caption)
                else:
                    self.url_caption_cache[src] = caption
                return src, caption

            # run image tasks
            download_tasks = [process_download(src) for src in download_srcs]
            caption_tasks  = [process_caption(src)  for src in caption_srcs]
            download_results_raw, caption_results_raw = await asyncio.gather(
                asyncio.gather(*download_tasks, return_exceptions=True),
                asyncio.gather(*caption_tasks,  return_exceptions=True),
            )
            # download results
            image_contents: list[GptImageContent] = []
            for res in download_results_raw:
                if isinstance(res, BaseException):
                    log.warning(f"process_download raised: {res}")
                    continue
                if res is None:
                    continue
                _, data = res
                content = utils.make_image_content(data)
                if content:
                    image_contents.append(content)
            # attachment results
            attachment_captions: dict[int, str] = {}
            url_captions: dict[str, str] = {}
            for res in caption_results_raw:
                if isinstance(res, BaseException):
                    log.warning(f"process_caption raised: {res}")
                    continue
                if res is None:
                    continue
                src, caption = res
                if isinstance(src, tuple):  # (idx, att)
                    attachment_captions[src[0]] = caption
                else:
                    url_captions[src] = caption
            # return
            return DiscordMessageResolvedImages(backmsg.id, image_contents, attachment_captions, url_captions)

        # run resource tasks
        quote_tasks = [resolve_quote(backmsg) for backmsg in backread]
        image_tasks = [resolve_images(backmsg) for backmsg in backread]
        quote_results_raw, image_results_raw = await asyncio.gather(
            asyncio.gather(*quote_tasks, return_exceptions=True),
            asyncio.gather(*image_tasks, return_exceptions=True),
        )
        # quote results
        all_resolved_quotes: dict[int, discord.Message | None] = {}
        for res in quote_results_raw:
            if isinstance(res, BaseException):
                log.warning(f"resolve_quote raised: {res}")
                continue
            msg_id, quote = res
            all_resolved_quotes[msg_id] = quote
        # image results
        all_resolved_images: dict[int, DiscordMessageResolvedImages] = {}
        for res in image_results_raw:
            if isinstance(res, BaseException):
                log.warning(f"resolve_images raised: {res}")
                continue
            all_resolved_images[res.message_id] = res

        # Pass 3: Parse each message and attach images

        parsed_messages: list[ParsedMessageResult] = []

        for n, backmsg in enumerate(backread):
            try:
                quote = all_resolved_quotes.get(backmsg.id)
                resolved = all_resolved_images.get(backmsg.id) or DiscordMessageResolvedImages(backmsg.id, [], {}, {})

                message_obj, message_inline_objs = await self.parse_discord_message(
                    backmsg,
                    quote,
                    backread,
                    max_quote_length,
                    max_file_length,
                    resolved.attachment_captions,
                    resolved.url_captions,
                    exhaustive=True,
                    recursive=True,
                )
                text_content = xmltodict.unparse(message_obj, full_document=False)
                for before, after_obj in message_inline_objs.items():
                    text_content = text_content.replace(before, xmltodict.unparse(after_obj, full_document=False))

                text_tokens  = len(self.encoding.encode(text_content))
                image_tokens = 1120 * len(resolved.image_contents)
                total_tokens = text_tokens + image_tokens
                content: str | list[GptImageContent]
                if resolved.image_contents:
                    content = [{"type": "text", "text": text_content}, *resolved.image_contents]
                    role = "user"
                else:
                    content = text_content
                    role = "assistant" if backmsg.author.id == self.bot.user.id else "user"

                gpt_msg = {
                    "role": role,
                    "content": content
                }
                parsed_messages.append(ParsedMessageResult(gpt_msg, total_tokens, len(resolved.image_contents)))

            except Exception as exc:
                log.warning(f"build_message_history_context: failed to parse message {backmsg.id}: {type(exc).__name__}: {exc}")

        # Pass 4: trim to token budget and return

        cumulative = 0
        cutoff = len(parsed_messages)
        for i, msg in enumerate(parsed_messages):
            cumulative += msg.tokens
            if i > 0 and cumulative > max_backread_tokens:
                cutoff = i
                break
        trimmed = parsed_messages[:cutoff]
        result.messages = len(trimmed)
        result.images = sum(msg.num_images for msg in trimmed)
        result.tokens.backread = sum(msg.tokens for msg in trimmed)

        return [msg.gpt_message for msg in reversed(trimmed)]


    async def fetch_and_normalize(self, message: discord.Message, src: ImageSource, max_image_size: int) -> bytes | None:
        """Fetch an attachment or URL and return normalized image bytes, or None on failure."""
        try:
            if isinstance(src, tuple):
                idx, attachment = src
                fp_before = BytesIO()
                imagescanner: commands.Cog | None = self.bot.get_cog("ImageScanner")
                if imagescanner and message.id in imagescanner.image_cache:  # type: ignore
                    _, image_bytes = imagescanner.image_cache.get(message.id, ({}, {}))  # type: ignore
                    if message.id in image_bytes:
                        fp_before = BytesIO(image_bytes[idx])
                if fp_before.getbuffer().nbytes == 0:
                    await attachment.save(fp_before, seek_begin=True)
            else:
                async with self.session.get(src) as response:
                    response.raise_for_status()
                    fp_before = BytesIO(await response.read())

            fp_after = await asyncio.to_thread(utils.normalize_image, fp_before, max_image_size ** 2)
            del fp_before
            return fp_after if fp_after else None

        except Exception as exc:
            src_label = attachment.url if isinstance(src, tuple) else src
            log.warning(f"_fetch_and_normalize {src_label}: {type(exc).__name__}: {exc}")
            return None


    async def parse_discord_message(
        self,
        message: discord.Message,
        quote: discord.Message | None,
        backread: list[discord.Message],
        max_quote_length: int,
        max_file_length: int,
        attachment_captions: dict[int, str] | None,
        url_captions: dict[str, str] | None,
        exhaustive: bool,
        recursive: bool,
    ) -> tuple[StructuredObject, dict[str, StructuredObject]]:
        """
        Converts a message into a dictionary of structured information that may then be unparsed into xml.
        Also returns a dictionary of inline objects to be injected back into the final string.
        """
        assert message.guild
        inline_objs: dict[str, dict[str, Any]] = {}
        obj: dict[str, Any] = {
            "@time": message.created_at.astimezone().strftime(constants.DATETIME_FORMATTING),
            "@username": message.author.name,
        }
        if isinstance(message.author, discord.Member) and message.author.nick:
            obj["@nickname"] = message.author.nick
        starting_len = len(obj)
        if message != backread[0] and self.is_busy(message.id):
            obj["error"] = "This message is currently being processed"
            return ({"chat_message": obj}, inline_objs)
        # generated image
        if message.attachments and len(message.attachments) == 1 and message.author == message.guild.me:
            imagescanner: commands.Cog | None = self.bot.get_cog("ImageScanner")
            metadata: dict[str, Any] = await imagescanner.grab_metadata_dict(message)  # type: ignore
            if metadata and metadata.get("Prompt"):
                obj["generated_image"] = {
                    "@filename": message.attachments[0].filename,
                    "@dimensions": metadata.get("Size", "unknown"),
                    "prompt": utils.parse_prompt(metadata["Prompt"]),
                }
        # quote
        if quote and exhaustive and recursive and "generated_image" not in obj:
            quoted_message_obj, quoted_message_inlines = await self.parse_discord_message(
                quote, None, backread, max_quote_length, max_file_length, None, None,
                exhaustive=quote not in backread, recursive=False
            )
            obj["quote"] = quoted_message_obj
            inline_objs.update(quoted_message_inlines)
        # text content
        if message.is_system():
            obj["action"] = "Joined the server" if message.type == discord.MessageType.new_member else message.system_content
        elif message.content:
            content = message.content
            for mentioned in message.mentions + message.role_mentions:
                content = content.replace(mentioned.mention, f"@{mentioned.name}")
            for mentioned_ch in message.channel_mentions:
                content = content.replace(mentioned_ch.mention, f"#{mentioned_ch.name}")
            for i, message_link in enumerate(constants.DISCORD_MESSAGE_LINK_PATTERN.finditer(content)):
                guild_id, channel_id, message_id = [int(num) for num in message_link.groups()]
                link_obj = {}
                if message.guild.id != guild_id:
                    link_obj["@source"] = "Outside this server"
                elif message.channel.id != channel_id:
                    channel = message.guild.get_channel_or_thread(channel_id)
                    link_obj["@channel"] = f"#{channel.name}" if channel else "unknown"
                inline_objs[message_link.group(0)] = {
                    "message_link": {
                        "#text": "...",
                        **link_obj,
                    }}
                # Add quote for linked message if it is the first
                if i == 0 and exhaustive and recursive and "generated_image" not in obj:
                    try:
                        linked = await self.bot.get_guild(guild_id).get_channel(channel_id).fetch_message(message_id) # type: ignore
                    except (AttributeError, discord.NotFound):
                        continue
                    linked_message_obj, linked_message_inlines = await self.parse_discord_message(
                        linked, None, backread, max_quote_length, max_file_length, None, None,
                        exhaustive=linked not in backread, recursive=False
                    )
                    obj["linked_message"] = {**link_obj, **linked_message_obj}
                    inline_objs.update(linked_message_inlines)
            if not exhaustive and len(content) > max_quote_length:
                content = content[:max_quote_length - 3] + "..."
                obj["@truncated"] = "true"
            obj["content"] = content
        # attachments
        if "generated_image" not in obj:
            attachments = []
            total_file_length = 0
            for i, attachment in enumerate(message.attachments):
                att_obj = {"@filename": attachment.filename}
                if exhaustive and attachment.content_type and attachment.content_type.startswith("text") and total_file_length < max_file_length:
                    if file_content := await self.read_text_file(attachment, max_file_length):
                        att_obj["content"] = file_content
                if attachment_captions and i in attachment_captions:
                    att_obj["caption"] = attachment_captions[i]
                attachments.append(att_obj)
            utils.add_xml_group(obj, attachments, "attachments")
        # linked images
        if url_captions:
            linked_images = []
            for url, caption in url_captions.items():
                linked_images.append({"@url": url, "caption": caption})
            utils.add_xml_group(obj, linked_images, "linked_images")
        # stickers
        stickers = []
        for sticker in message.stickers:
            stickers.append({"#text": sticker.name})
        utils.add_xml_group(obj, stickers, "stickers")
        # embeds
        embeds = []
        for embed in message.embeds:
            embed_obj = {}
            if embed.title:
                embed_obj["title"] = embed.title
            if embed.description:
                embed_obj["description"] = embed.description if exhaustive else "..."
            if embed.image and embed.image.url:
                embed_obj["image"] = embed.image.url
            if embed.thumbnail and embed.thumbnail.url:
                embed_obj["thumbnail"] = embed.thumbnail.url
            fields = []
            for field in embed.fields:
                fields.append({
                    "@name": field.name,
                    "#text": field.value,
                })
            if len(fields) > 0 and exhaustive:
                utils.add_xml_group(embed_obj, fields, "fields")
            if embed_obj:
                embeds.append(embed_obj)
        utils.add_xml_group(obj, embeds, "embeds")
        # buttons
        if exhaustive and "generated_image" not in obj:
            buttons = []
            for component in message.components:
                if isinstance(component, discord.ActionRow):
                    for subcomponent in component.children:
                        if isinstance(subcomponent, discord.Button):
                            buttons.append({"#text": utils.button_label(subcomponent)})
                elif isinstance(component, discord.Button):
                    buttons.append({"#text": utils.button_label(component)})
            utils.add_xml_group(obj, buttons, "buttons")
        # poll
        if message.poll:
            poll = {"question": message.poll.question}
            if exhaustive:
                answers = []
                for answer in message.poll.answers:
                    answers.append({
                        "@votes": str(answer.vote_count),
                        "#text": answer.text,
                    })
                utils.add_xml_group(poll, answers, "answers")
            obj["poll"] = poll
        # etc
        if len(obj) == starting_len:
            obj["error"] = "Message empty or not supported"
        # reactions
        if exhaustive and "generated_image" not in obj:
            reactions = []
            for reaction in message.reactions[:5]:
                reaction_obj = {
                    "@count": str(reaction.count),
                    "#text": reaction.emoji if isinstance(reaction.emoji, str) else reaction.emoji.name
                }
                if reaction.me:
                    reaction_obj["self_reacted"] = "true"
                reactions.append(reaction_obj)
            utils.add_xml_group(obj, reactions, "reactions")
        # done
        return ({"chat_message": obj}, inline_objs)
    

    async def read_text_file(self, attachment: discord.Attachment, max_file_length: int) -> str | None:
        fp = BytesIO()
        try:
            await attachment.save(fp, seek_begin=True)
            file_content = fp.getvalue().decode('utf-8')
        except (discord.DiscordException, UnicodeDecodeError) as error:
            log.warning(f"Processing text attachment {attachment.filename}: {type(error).__name__}: {error}")
            return None
        if len(file_content) > max_file_length + 10:
            file_content = f"{file_content[:max_file_length//2]}\n(...)\n{file_content[-max_file_length//2:]}"
        return file_content
