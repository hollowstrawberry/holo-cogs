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
from gptmemory.schema import GptImageContent, CompletionResult, GptMessage, ImageSource, ParsedMessageResult, StructuredObject
from gptmemory.schema import DiscordMessageImageCandidates, DiscordMessageResolvedImages


log = logging.getLogger("gptmemory.context")


class ContextBuilder:
    def __init__(
        self,
        bot: Red,
        config: Config,
        session: aiohttp.ClientSession,
        encoding: tiktoken.Encoding,
        execute_captioner: Callable[[commands.Context, GptImageContent, CompletionResult], Awaitable[str]],
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
        result: CompletionResult,
    ) -> list[GptMessage]:
        
        assert ctx.guild
        config = await self.config.guild(ctx.guild).all()
        imagescanner: commands.Cog | None = self.bot.get_cog("ImageScanner")

        # Pass 1: grab quoted messages

        quotes_needed: dict[int, int | None] = {}
        for n, backmsg in enumerate(backread):
            ref = backmsg.reference
            if ref and not (len(backread) > n + 1 and ref.message_id == backread[n + 1].id):  # prevent consecutive quote chains
                quotes_needed[backmsg.id] = ref.message_id
            else:
                quotes_needed[backmsg.id] = None

        async def resolve_quote(backmsg: discord.Message) -> tuple[int, discord.Message | None]:
            quote_id = quotes_needed[backmsg.id]
            if quote_id is None:
                return backmsg.id, None
            if backmsg.reference and backmsg.reference.cached_message:
                return backmsg.id, backmsg.reference.cached_message
            try:
                return backmsg.id, await backmsg.channel.fetch_message(quote_id)
            except discord.DiscordException:
                return backmsg.id, None

        quote_tasks = [resolve_quote(backmsg) for backmsg in backread]
        quote_results_raw = await asyncio.gather(*quote_tasks, return_exceptions=True)
        all_resolved_quotes: dict[int, discord.Message | None] = {}
        for res in quote_results_raw:
            if isinstance(res, BaseException):
                log.warning(f"resolve_quote raised: {res}")
                continue
            msg_id, quote = res
            all_resolved_quotes[msg_id] = quote

        # Pass 2: decide which images will be downloaded and which will be captioned

        def extract_candidates(msg: discord.Message) -> list[ImageSource]:
            att_candidates: list[ImageSource] = [
                ImageSource(msg.id, attachment=att, att_index=i)
                for i, att in enumerate(msg.attachments)
                if att.content_type and att.content_type.startswith("image/")
            ]
            if att_candidates:
                return att_candidates
            url_candidates: list[ImageSource] = []
            for embed in msg.embeds:
                if embed.image and embed.image.url:
                    url_candidates.append(ImageSource(msg.id, url=embed.image.url))
                if embed.thumbnail and embed.thumbnail.url:
                    url_candidates.append(ImageSource(msg.id, url=embed.thumbnail.url))
            for match in constants.URL_PATTERN.findall(msg.content or ""):
                if match.endswith(constants.IMAGE_EXTENSIONS):
                    url_candidates.append(ImageSource(msg.id, url=match))
            return url_candidates

        all_candidates: dict[int, DiscordMessageImageCandidates] = {}
        first_appearance: dict[int, int] = {}
        priority_remaining = config["max_images"]
        for backmsg in backread:
            quote = all_resolved_quotes.get(backmsg.id)
            backmsg_candidates = extract_candidates(backmsg)
            quote_candidates   = extract_candidates(quote) if quote else []
            candidates = backmsg_candidates + quote_candidates
            if not candidates:
                continue
            first_appearance[backmsg.id] = first_appearance.get(backmsg.id) or backmsg.id
            if quote:
                first_appearance[quote.id] = first_appearance.get(quote.id) or backmsg.id
            # share image budget between base message and its quoted message
            priority_slots = max(0, priority_remaining)
            priority_list = candidates[:priority_slots]
            caption_list  = candidates[priority_slots:]
            priority_remaining -= len(priority_list)
            # save them separately
            def filter_sources(msg: discord.Message) -> tuple[list[ImageSource], list[ImageSource]]:
                return ([src for src in priority_list if src.message_id == msg.id], [src for src in caption_list if src.message_id == msg.id])
            if backmsg.id not in all_candidates:
                all_candidates[backmsg.id] = DiscordMessageImageCandidates(backmsg, *filter_sources(backmsg))
            if quote and quote.id not in all_candidates:
                all_candidates[quote.id] = DiscordMessageImageCandidates(quote, *filter_sources(quote))
        
        # Pass 3: grab images

        async def resolve_images(backmsg: discord.Message) -> DiscordMessageResolvedImages:
            generated_image: dict[str, str] | None = None
            if backmsg.attachments and len(backmsg.attachments) == 1 and backmsg.author == ctx.me:
                if "gptimage" in backmsg.attachments[0].filename:
                    generated_image = {"type": "gptimage"}
                elif imagescanner:
                    generated_image = await getattr(imagescanner, "grab_metadata_dict")(backmsg)
            
            async def process_priority(src: ImageSource) -> tuple[ImageSource, bytes, str] | None:
                data, caption = None, ""
                if src.attachment:
                    _, data = self.attachment_image_cache.get(src.attachment.id, (None, None))
                    _, caption = self.attachment_caption_cache.get(src.attachment.id, (None, ""))
                elif src.url:
                    data = self.url_image_cache.get(src.url)
                    caption = self.url_caption_cache.get(src.url, "")
                if not data:
                    data = await self.fetch_and_normalize(src, max_image_resolution=config["max_image_resolution"])
                if not data:
                    log.warning(f"image data is None for {src}")
                    return None
                if not caption and not generated_image:
                    data_thumbnail = await asyncio.to_thread(utils.normalize_image, data, None, config["max_caption_resolution"])
                    image_content = utils.make_image_content(data_thumbnail or b'', low_detail=True)
                    caption = await self.execute_captioner(ctx, image_content, result)
                if not caption and not generated_image:
                    log.warning(f"caption is None for {src}")
                if src.attachment:
                    self.attachment_image_cache[src.attachment.id] = (src.att_index, data)
                    self.attachment_caption_cache[src.attachment.id] = (src.att_index, caption)
                elif src.url:
                    self.url_image_cache[src.url] = data
                    self.url_caption_cache[src.url] = caption
                return src, data, caption

            async def process_caption(src: ImageSource) -> tuple[ImageSource, str] | None:
                if generated_image and generated_image.get("Prompt"):
                    return None
                caption = None
                if src.attachment:
                    _, caption = self.attachment_caption_cache.get(src.attachment.id, (None, None))
                elif src.url:
                    caption = self.url_caption_cache.get(src.url)
                if caption:
                    return src, caption
                data = await self.fetch_and_normalize(src, thumbnail_size=config["max_caption_resolution"])
                if data is None:
                    log.warning(f"image data is None for {src}")
                    return None
                image_content = utils.make_image_content(data, low_detail=True)
                caption = await self.execute_captioner(ctx, image_content, result)
                if caption is None:
                    log.warning(f"caption is None for {src}")
                    return None
                if src.attachment:
                    self.attachment_caption_cache[src.attachment.id] = (src.att_index, caption)
                elif src.url:
                    self.url_caption_cache[src.url] = caption
                return src, caption
            
            candidates = all_candidates[backmsg.id]
            all_srcs = (candidates.download + candidates.caption)[:constants.MAX_IMAGES_PER_MESSAGE]
            priority_srcs = [s for s in all_srcs if s in candidates.download]
            caption_srcs  = [s for s in all_srcs if s in candidates.caption]
            priority_tasks = [process_priority(src) for src in priority_srcs]
            caption_tasks  = [process_caption(src)  for src in caption_srcs]
            priority_results_raw, caption_results_raw = await asyncio.gather(
                asyncio.gather(*priority_tasks, return_exceptions=True),
                asyncio.gather(*caption_tasks,  return_exceptions=True),
            )
            image_contents: list[GptImageContent] = []
            attachment_captions: dict[int, str] = {}
            url_captions: dict[str, str] = {}
            for res in priority_results_raw:
                if isinstance(res, BaseException):
                    log.warning(f"process_download raised: {res}")
                    continue
                if res is None:
                    continue
                src, data, caption = res
                image_contents.append(utils.make_image_content(data))
                if src.attachment:
                    attachment_captions[src.att_index] = caption
                elif src.url:
                    url_captions[src.url] = caption
            for res in caption_results_raw:
                if isinstance(res, BaseException):
                    log.warning(f"process_caption raised: {res}")
                    continue
                if res is None:
                    continue
                src, caption = res
                if src.attachment:
                    attachment_captions[src.att_index] = caption
                elif src.url:
                    url_captions[src.url] = caption
            return DiscordMessageResolvedImages(backmsg.id, image_contents, attachment_captions, url_captions, generated_image)

        image_tasks = [resolve_images(src.message) for src in all_candidates.values()]
        image_results_raw = await asyncio.gather(*image_tasks, return_exceptions=True)
        all_resolved_images: dict[int, DiscordMessageResolvedImages] = {}
        for res in image_results_raw:
            if isinstance(res, BaseException):
                log.warning(f"resolve_images raised: {res}")
                continue
            all_resolved_images[res.message_id] = res
 
        # Pass 4: Parse each message and attach images

        async def parse_message_and_images(backmsg: discord.Message) -> ParsedMessageResult:
            quote = all_resolved_quotes.get(backmsg.id)
            images = all_resolved_images.get(backmsg.id)
            quoted_images = all_resolved_images.get(quote.id) if quote else None

            message_obj, message_inline_objs = await self.parse_discord_message(
                backmsg, quote, backread, all_resolved_images,
                config["max_quote"], config["max_text_file"],
                exhaustive=True, recursive=True,
            )
            text_content = xmltodict.unparse(message_obj, full_document=False)
            for before, after_obj in message_inline_objs.items():
                text_content = text_content.replace(before, xmltodict.unparse(after_obj, full_document=False))

            image_contents: list[GptImageContent] = []
            if images and first_appearance[images.message_id] == backmsg.id:
                image_contents.extend(images.image_contents)
            if quoted_images and first_appearance[quoted_images.message_id] == backmsg.id:
                image_contents.extend(quoted_images.image_contents)
            text_tokens  = len(self.encoding.encode(text_content))
            image_tokens = 1120 * len(image_contents)
            total_tokens = text_tokens + image_tokens
            content: str | list[GptImageContent]

            if image_contents:
                content = [{"type": "text", "text": text_content}, *image_contents]
                role = "user"
            else:
                content = text_content
                role = "user"#"assistant" if backmsg.author == ctx.me else "user"

            gpt_msg = {
                "role": role,
                "content": content
            }
            return ParsedMessageResult(backmsg.id, gpt_msg, total_tokens, len(image_contents))

        parse_tasks = [parse_message_and_images(backmsg) for backmsg in backread]
        parse_results_raw = await asyncio.gather(*parse_tasks, return_exceptions=True)
        all_parsed_messages: dict[int, ParsedMessageResult] = {}
        for res in parse_results_raw:
            if isinstance(res, BaseException):
                log.warning(f"parse_message_and_images raised: {res}")
                continue
            all_parsed_messages[res.message_id] = res

        # Pass 5: trim to token budget and return

        parsed_messages = [all_parsed_messages[backmsg.id] for backmsg in backread if backmsg.id in all_parsed_messages]
        cumulative = 0
        cutoff = len(parsed_messages)
        for i, msg in enumerate(parsed_messages):
            cumulative += msg.tokens
            if i > 0 and cumulative > config["backread_tokens"]:
                cutoff = i + 1  # it's fine to go over
                break
        parsed_messages = parsed_messages[:cutoff]
        result.messages = len(parsed_messages)
        result.images = sum(msg.num_images for msg in parsed_messages)
        result.tokens.backread = sum(msg.tokens for msg in parsed_messages)

        return [msg.gpt_message for msg in reversed(parsed_messages)]


    async def fetch_and_normalize(
        self,
        src: ImageSource,
        max_image_resolution: int | None = None,
        thumbnail_size: int | None = None,
    ) -> bytes | None:
        
        assert max_image_resolution or thumbnail_size
        max_pixels = max_image_resolution ** 2 if max_image_resolution else None
        try:
            if src.attachment:
                fp_before = BytesIO()
                imagescanner: commands.Cog | None = self.bot.get_cog("ImageScanner")
                if imagescanner and src.message_id in getattr(imagescanner, "image_cache"):
                    _, image_bytes = getattr(imagescanner, "image_cache").get(src.message_id, ({}, {}))
                    if src.att_index in image_bytes:
                        fp_before = BytesIO(image_bytes[src.att_index])
                if fp_before.getbuffer().nbytes == 0:
                    await src.attachment.save(fp_before, seek_begin=True)
            elif src.url:
                async with self.session.get(src.url, headers=constants.MEDIA_HEADERS) as response:
                    response.raise_for_status()
                    fp_before = BytesIO(await response.read())

            fp_after = await asyncio.to_thread(utils.normalize_image, fp_before, max_pixels, thumbnail_size)
            del fp_before
            return fp_after if fp_after else None

        except Exception as error:
            src_label = src.attachment.url if src.attachment else src.url
            log.warning(f"fetch_and_normalize {src_label}: {type(error).__name__}: {error}")
            return None


    async def parse_discord_message(
        self,
        message: discord.Message,
        quote: discord.Message | None,
        backread: list[discord.Message],
        images: dict[int, DiscordMessageResolvedImages],
        max_quote_length: int,
        max_file_length: int,
        exhaustive: bool,
        recursive: bool,
    ) -> tuple[StructuredObject, dict[str, StructuredObject]]:
        """
        Converts a message into a dictionary of structured information that may then be unparsed into xml.
        Also returns a dictionary of inline objects to be injected back into the final string.
        """
        assert message.guild
        current_images = images.get(message.id)
        generated_image = current_images.generated_image if current_images else None
        attachment_captions = current_images.attachment_captions if current_images else None
        url_captions = current_images.url_captions if current_images else None
        
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
        if generated_image and generated_image.get("Prompt"):
            obj["stable_diffusion_image"] = {
                "@filename": message.attachments[0].filename,
                "@dimensions": generated_image.get("Size", "unknown"),
                "prompt": utils.parse_prompt(generated_image["Prompt"]),
            }
        # quote
        if quote and exhaustive and recursive and not generated_image:
            quoted_message_obj, quoted_message_inlines = await self.parse_discord_message(
                quote, None, backread, images,
                max_quote_length, max_file_length,
                exhaustive=quote not in backread, recursive=False
            )
            obj["quote"] = quoted_message_obj
            inline_objs.update(quoted_message_inlines)
        # text content
        if message.is_system():
            obj["action"] = "Joined the server" if message.type == discord.MessageType.new_member else message.system_content
        elif message.content:
            content = utils.clean_content(message.content)
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
                if i == 0 and exhaustive and recursive and not generated_image:
                    try:
                        linked = await self.bot.get_guild(guild_id).get_channel(channel_id).fetch_message(message_id) # type: ignore
                    except (AttributeError, discord.NotFound):
                        continue
                    linked_message_obj, linked_message_inlines = await self.parse_discord_message(
                        linked, None, backread, images,
                        max_quote_length, max_file_length,
                        exhaustive=linked not in backread, recursive=False
                    )
                    obj["linked_message"] = {**link_obj, **linked_message_obj}
                    inline_objs.update(linked_message_inlines)
            if not exhaustive and len(content) > max_quote_length:
                content = content[:max_quote_length - 3] + "..."
                obj["@truncated"] = "true"
            obj["content"] = content
        # attachments
        if not generated_image or not generated_image.get("Prompt"):
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
        linked_images = []
        if url_captions:
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
            if embed.author:
                embed_obj["header"] = embed.author.name
            if embed.title:
                embed_obj["title"] = embed.title
            if embed.description:
                embed_obj["description"] = utils.clean_content(embed.description)# if exhaustive else "..."
            if embed.url and embed.url[:max_quote_length] not in obj.get("content", ""):
                embed_obj["url"] = embed.url
            if embed.image and embed.image.url and not any(link["@url"] == embed.image.url for link in linked_images):
                embed_obj["image"] = embed.image.url
            if embed.thumbnail and embed.thumbnail.url and not any(link["@url"] == embed.thumbnail.url for link in linked_images):
                embed_obj["thumbnail"] = embed.thumbnail.url
            if embed.fields and exhaustive:
                fields = []
                for field in embed.fields:
                    fields.append({
                        "@name": field.name,
                        "#text": utils.clean_content(str(field.value)),
                    })
                utils.add_xml_group(embed_obj, fields, "fields")
            if embed.footer:
                embed_obj["footer"] = embed.footer.text
            if embed_obj:
                embeds.append(embed_obj)
        utils.add_xml_group(obj, embeds, "embeds")
        # buttons
        if exhaustive and not generated_image:
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
        if exhaustive:
            reactions = []
            for reaction in message.reactions[:5]:
                if reaction.emoji == "🔎":
                    continue
                reaction_obj = {
                    "@count": str(reaction.count),
                    "#text": reaction.emoji if isinstance(reaction.emoji, str) else f":{reaction.emoji.name}:"
                }
                if reaction.me:
                    reaction_obj["@self_reacted"] = "true"
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
