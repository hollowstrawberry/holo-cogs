import logging
import asyncio
import discord
import tiktoken
import xmltodict
from io import BytesIO
from typing import Any
from expiringdict import ExpiringDict
from redbot.core import commands

from agent import utils as utils
from agent import constants as constants
from agent.base import AgentCogBase, AgentCogGuildConfig
from agent.schema import AgentImageContent, CompletionResult, AgentMessage, ImageSource, ParsedMessageResult, StructuredObject
from agent.schema import DiscordMessageImageCandidates, DiscordMessageResolvedImages

log = logging.getLogger("agent.context")


class ContextBuilder:
    def __init__(self, cog: AgentCogBase):
        self.bot = cog.bot
        self.config = cog.config
        self.session = cog.session
        self.execute_captioner = cog.execute_captioner
        self.is_busy = cog.is_busy
        self.attachment_image_cache: dict[int, tuple[int, bytes]]  = ExpiringDict(max_len=25, max_age_seconds=24*60*60)
        self.url_image_cache: dict[str, bytes]                     = ExpiringDict(max_len=25, max_age_seconds=24*60*60)
        self.attachment_caption_cache: dict[int, tuple[int, str]]  = ExpiringDict(max_len=200, max_age_seconds=24*60*60)
        self.url_caption_cache: dict[str, str]                     = ExpiringDict(max_len=200, max_age_seconds=24*60*60)
        self.quote_lock: dict[int, asyncio.Lock]                   = ExpiringDict(max_len=25, max_age_seconds=120)
        self.url_lock: dict[str, asyncio.Lock]                     = ExpiringDict(max_len=25, max_age_seconds=120)

    async def build_context(
        self,
        ctx: commands.Context,
        backread: list[discord.Message],
        config: AgentCogGuildConfig,
        result: CompletionResult,
        encoding: tiktoken.Encoding,
    ) -> list[AgentMessage]:
        return await ChatHistoryContext(self, ctx, backread, config, result, encoding).build()


class ChatHistoryContext:
    def __init__(
        self,
        builder: ContextBuilder,
        ctx: commands.Context,
        backread: list[discord.Message],
        config: AgentCogGuildConfig,
        result: CompletionResult,
        encoding: tiktoken.Encoding,
    ):
        self.builder = builder
        self.ctx = ctx
        self.backread = backread
        self.result = result
        self.encoding = encoding
        self.config = config
        self.all_candidates: dict[int, DiscordMessageImageCandidates] = {}
        self.first_appearance: dict[int, int] = {}
        self.all_resolved_quotes: dict[int, discord.Message | None] = {}
        self.all_resolved_images: dict[int, DiscordMessageResolvedImages] = {}


    async def build(self) -> list[AgentMessage]:
        assert self.ctx.guild

        # Pass 1: grab quoted messages
        quotes: dict[int, int | None] = {}
        for n, backmsg in enumerate(self.backread):
            ref = backmsg.reference
            if ref and not (len(self.backread) > n + 1 and ref.message_id == self.backread[n + 1].id):  # prevent consecutive quote chains
                quotes[backmsg.id] = ref.message_id
            else:
                quotes[backmsg.id] = None
        quote_tasks = [self.resolve_quote(quote_id, backmsg) for backmsg in self.backread if (quote_id := quotes[backmsg.id])]
        quote_results_raw = await asyncio.gather(*quote_tasks, return_exceptions=True)
        for res in quote_results_raw:
            if isinstance(res, BaseException):
                log.warning(f"resolve_quote raised: {res}")
                continue
            msg_id, quote = res
            self.all_resolved_quotes[msg_id] = quote

        # Pass 2: decide which images will be sent in full and which will be captioned
        priority_remaining = self.config.max_images.value
        for backmsg in self.backread:
            quote = self.all_resolved_quotes.get(backmsg.id)
            backmsg_candidates = self.extract_candidates(backmsg)
            quote_candidates   = self.extract_candidates(quote) if quote else []
            candidates = backmsg_candidates + quote_candidates
            if not candidates:
                continue
            self.first_appearance.setdefault(backmsg.id, backmsg.id)
            if quote:
                self.first_appearance.setdefault(quote.id, backmsg.id)
            # share image budget between base message and its quoted message
            priority_slots = max(0, priority_remaining)
            priority_list = candidates[:priority_slots]
            caption_list  = candidates[priority_slots:]
            priority_remaining -= len(priority_list)
            # save them separately
            for msg in [backmsg, quote]:
                if msg and msg.id not in self.all_candidates:
                    self.all_candidates[msg.id] = DiscordMessageImageCandidates(msg,
                        [s for s in priority_list if s.message_id == msg.id],
                        [s for s in caption_list if s.message_id == msg.id],
                    )

        # Pass 3: grab images
        image_tasks = [self.resolve_images(src.message) for src in self.all_candidates.values()]
        image_results_raw = await asyncio.gather(*image_tasks, return_exceptions=True)
        for res in image_results_raw:
            if isinstance(res, BaseException):
                log.warning(f"resolve_images raised: {res}")
                continue
            self.all_resolved_images[res.message_id] = res
 
        # Pass 4: Parse each message and attach images
        parse_tasks = [self.parse_message_and_images(backmsg) for backmsg in self.backread]
        parse_results_raw = await asyncio.gather(*parse_tasks, return_exceptions=True)
        all_parsed_messages: dict[int, ParsedMessageResult] = {}
        for res in parse_results_raw:
            if isinstance(res, BaseException):
                log.warning(f"parse_message_and_images raised: {res}")
                continue
            all_parsed_messages[res.message_id] = res

        # Pass 5: trim to token budget and return
        parsed_messages = [parsed_message for backmsg in self.backread if (parsed_message := all_parsed_messages.get(backmsg.id))]
        cumulative = 0
        cutoff = len(parsed_messages)
        for i, msg in enumerate(parsed_messages):
            cumulative += msg.tokens
            if i > 0 and cumulative > self.config.backread_tokens.value:
                cutoff = i + 1  # it's fine to go over
                break
        parsed_messages = parsed_messages[:cutoff]
        self.result.messages = len(parsed_messages)
        self.result.images = sum(msg.num_images for msg in parsed_messages)
        self.result.tokens.backread = sum(msg.tokens for msg in parsed_messages)

        return [msg.gpt_message for msg in reversed(parsed_messages)]
        

    async def resolve_quote(self, quote_id: int, backmsg: discord.Message) -> tuple[int, discord.Message | None]:
        lock = self.builder.quote_lock.setdefault(quote_id, asyncio.Lock())
        async with lock:
            if backmsg.reference and backmsg.reference.cached_message:
                return backmsg.id, backmsg.reference.cached_message
            try:
                return backmsg.id, await backmsg.channel.fetch_message(quote_id)
            except discord.DiscordException:
                return backmsg.id, None
    

    @staticmethod
    def extract_candidates(msg: discord.Message) -> list[ImageSource]:
        att_candidates: list[ImageSource] = [
            ImageSource(msg.id, attachment=att, att_index=i)
            for i, att in enumerate(msg.attachments)
            if att.content_type and att.content_type.startswith("image/")
        ]
        if att_candidates:
            return att_candidates
        url_candidates: set[ImageSource] = set()
        for embed in msg.embeds:
            if embed.image and embed.image.url:
                url_candidates.add(ImageSource(msg.id, url=embed.image.url))
            if embed.thumbnail and embed.thumbnail.url:
                url_candidates.add(ImageSource(msg.id, url=embed.thumbnail.url))
        for match in constants.URL_PATTERN.findall(msg.content or ""):
            if match.endswith(constants.IMAGE_EXTENSIONS):
                url_candidates.add(ImageSource(msg.id, url=match))
        return list(url_candidates)


    async def resolve_images(self, backmsg: discord.Message) -> DiscordMessageResolvedImages:
        candidates = self.all_candidates[backmsg.id]
        generated_image: dict[str, str] | None = None
        if backmsg.attachments and len(backmsg.attachments) == 1 and backmsg.author == self.ctx.me:
            if "gptimage" in backmsg.attachments[0].filename:
                generated_image = {"type": "gptimage"}
            elif imagescanner := self.builder.bot.get_cog("ImageScanner"):
                generated_image = await getattr(imagescanner, "grab_metadata_dict")(backmsg)

        priority_srcs = candidates.priority[:constants.MAX_IMAGES_PER_MESSAGE]
        num_remaining = constants.MAX_IMAGES_PER_MESSAGE - len(priority_srcs)
        caption_srcs = candidates.caption[:num_remaining]
        priority_tasks = [self.process_image_full(src, generated_image) for src in priority_srcs]
        caption_tasks  = [self.process_image_caption(src, generated_image) for src in caption_srcs]
        results_raw = await asyncio.gather(*priority_tasks, *caption_tasks, return_exceptions=True)
    
        image_contents: list[AgentImageContent] = []
        attachment_captions: dict[int, str] = {}
        url_captions: dict[str, str] = {}
        for res in results_raw:
            if isinstance(res, BaseException):
                log.warning(f"process_download raised: {res}")
                continue
            if res is None:
                continue
            src, caption, data = res
            if data:
                image_contents.append(utils.make_image_content(data))
            if src.attachment:
                attachment_captions[src.att_index] = caption
            elif src.url:
                url_captions[src.url] = caption
                
        return DiscordMessageResolvedImages(backmsg.id, image_contents, attachment_captions, url_captions, generated_image)


    async def process_image_full(self, src: ImageSource, generated_image: Any) -> tuple[ImageSource, str, bytes] | None:
        key = src.attachment.url if src.attachment else src.url
        if not key:
            return
        lock = self.builder.url_lock.setdefault(key, asyncio.Lock())
        async with lock:
            data, caption = None, ""
            if src.attachment:
                _, data = self.builder.attachment_image_cache.get(src.attachment.id, (None, None))
                _, caption = self.builder.attachment_caption_cache.get(src.attachment.id, (None, ""))
            elif src.url:
                data = self.builder.url_image_cache.get(src.url)
                caption = self.builder.url_caption_cache.get(src.url, "")
            if not data:
                data = await self.fetch_and_normalize(src, max_resolution=self.config.max_image_resolution.value)
                if not data:
                    log.warning(f"image data is None for {src}")
                    return None
            if not caption and not generated_image:
                data_thumbnail = await asyncio.to_thread(utils.normalize_image, data, None, self.config.max_caption_resolution.value)
                image_content = utils.make_image_content(data_thumbnail or b'', low_detail=True)
                caption = await self.builder.execute_captioner(self.ctx, image_content, self.result)
                if not caption:
                    log.warning(f"caption is None for {src}")
            if src.attachment:
                self.builder.attachment_image_cache[src.attachment.id] = (src.att_index, data)
                self.builder.attachment_caption_cache[src.attachment.id] = (src.att_index, caption)
            elif src.url:
                self.builder.url_image_cache[src.url] = data
                self.builder.url_caption_cache[src.url] = caption
            return src, caption, data


    async def process_image_caption(self, src: ImageSource, generated_image: Any) -> tuple[ImageSource, str, None] | None:
        if generated_image and generated_image.get("Prompt"):
            return None
        key = src.attachment.url if src.attachment else src.url
        if not key:
            return
        lock = self.builder.url_lock.setdefault(key, asyncio.Lock())
        async with lock:
            caption = None
            if src.attachment:
                _, caption = self.builder.attachment_caption_cache.get(src.attachment.id, (None, None))
            elif src.url:
                caption = self.builder.url_caption_cache.get(src.url)
            if caption:
                return src, caption, None
            data = await self.fetch_and_normalize(src, thumbnail_size=self.config.max_caption_resolution.value)
            if data is None:
                log.warning(f"image data is None for {src}")
                return None
            image_content = utils.make_image_content(data, low_detail=True)
            caption = await self.builder.execute_captioner(self.ctx, image_content, self.result)
            if caption is None:
                log.warning(f"caption is None for {src}")
                return None
            if src.attachment:
                self.builder.attachment_caption_cache[src.attachment.id] = (src.att_index, caption)
            elif src.url:
                self.builder.url_caption_cache[src.url] = caption
            return src, caption, None


    async def fetch_and_normalize(self, src: ImageSource, max_resolution: int | None = None, thumbnail_size: int | None = None) -> bytes | None:
        assert max_resolution or thumbnail_size
        max_pixels = max_resolution ** 2 if max_resolution else None
        try:
            if src.attachment:
                fp_before = BytesIO()
                imagescanner: commands.Cog | None = self.builder.bot.get_cog("ImageScanner")
                if imagescanner and src.message_id in getattr(imagescanner, "image_cache"):
                    _, image_bytes = getattr(imagescanner, "image_cache").get(src.message_id, ({}, {}))
                    if src.att_index in image_bytes:
                        fp_before = BytesIO(image_bytes[src.att_index])
                if fp_before.getbuffer().nbytes == 0:
                    await src.attachment.save(fp_before, seek_begin=True)
            elif src.url:
                async with self.builder.session.get(src.url, headers=constants.MEDIA_HEADERS) as response:
                    response.raise_for_status()
                    fp_before = BytesIO(await response.read())

            fp_after = await asyncio.to_thread(utils.normalize_image, fp_before, max_pixels, thumbnail_size)
            del fp_before
            return fp_after if fp_after else None
        except Exception as error:
            src_label = src.attachment.url if src.attachment else src.url
            log.warning(f"fetch_and_normalize {src_label}: {type(error).__name__}: {error}")
            return None


    async def parse_message_and_images(self, backmsg: discord.Message) -> ParsedMessageResult:
        quote = self.all_resolved_quotes.get(backmsg.id)
        images = self.all_resolved_images.get(backmsg.id)
        quoted_images = self.all_resolved_images.get(quote.id) if quote else None

        message_obj, message_inline_objs = await self.parse_discord_message(backmsg, quote, exhaustive=True, recursive=True)
        text_content = xmltodict.unparse(message_obj, full_document=False)
        for before, after_obj in message_inline_objs.items():
            text_content = text_content.replace(utils.escape_xml(before), xmltodict.unparse(after_obj, full_document=False))

        image_contents: list[AgentImageContent] = []
        if images and self.first_appearance[images.message_id] == backmsg.id:
            image_contents.extend(images.image_contents)
        if quoted_images and self.first_appearance[quoted_images.message_id] == backmsg.id:
            image_contents.extend(quoted_images.image_contents)
        text_tokens  = len(self.encoding.encode(text_content))
        image_tokens = 1120 * len(image_contents)
        total_tokens = text_tokens + image_tokens
        content: str | list[AgentImageContent]

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


    async def parse_discord_message(
        self,
        message: discord.Message,
        quote: discord.Message | None,
        exhaustive: bool,
        recursive: bool,
    ) -> tuple[StructuredObject, dict[str, StructuredObject]]:
        """
        Converts a message into a dictionary of structured information that may then be unparsed into xml.
        Also returns a dictionary of inline objects to be injected back into the final string.
        """
        assert message.guild
        current_images = self.all_resolved_images.get(message.id)
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
        if message != self.backread[0] and self.builder.is_busy(message.id):
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
                quote, None, exhaustive=quote not in self.backread, recursive=False
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
                        linked = await self.builder.bot.get_guild(guild_id).get_channel(channel_id).fetch_message(message_id) # type: ignore
                    except (AttributeError, discord.NotFound):
                        continue
                    linked_message_obj, linked_message_inlines = await self.parse_discord_message(
                        linked, None, exhaustive=linked not in self.backread, recursive=False
                    )
                    obj["linked_message"] = {**link_obj, **linked_message_obj}
                    inline_objs.update(linked_message_inlines)
            if not exhaustive and len(content) > self.config.max_quote.value:
                content = content[:self.config.max_quote.value - 3] + "..."
                obj["@truncated"] = "true"
            obj["content"] = content
        # attachments
        if not generated_image or not generated_image.get("Prompt"):
            attachments = []
            total_file_length = 0
            for i, attachment in enumerate(message.attachments):
                att_obj = {"@filename": attachment.filename}
                if exhaustive and attachment.content_type and attachment.content_type.startswith("text") \
                        and total_file_length < self.config.max_text_file.value:
                    if file_content := await self.read_text_file(attachment):
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
            if embed.url and embed.url[:self.config.max_quote.value] not in obj.get("content", ""):
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
    

    async def read_text_file(self, attachment: discord.Attachment) -> str | None:
        max_length = self.config.max_text_file.value
        fp = BytesIO()
        try:
            await attachment.save(fp, seek_begin=True)
            file_content = fp.getvalue().decode('utf-8')
        except (discord.DiscordException, UnicodeDecodeError) as error:
            log.warning(f"Processing text attachment {attachment.filename}: {type(error).__name__}: {error}")
            return None
        if len(file_content) > max_length + 10:
            file_content = f"{file_content[:max_length//2]}\n(...)\n{file_content[-max_length//2:]}"
        return file_content
