import logging
import asyncio
import discord
import tiktoken
import xmltodict
from typing import Sequence
from itertools import accumulate, chain, repeat, zip_longest
from collections import Counter
from dataclasses import dataclass
from expiringdict import ExpiringDict
from redbot.core import commands

from agent import utils, constants
from agent.base import AgentCogBase
from agent.config import AgentCogGuildConfig
from agent.types import (
    ImageQuality, ImageSlot, ImageSource, ImageEntry, ImageScanner,
    StructuredObject, AgentMessage, ContextMessage, CompletionResult
)

log = logging.getLogger("agent.context")


@dataclass(frozen=True)
class AgentChatContext:
    _messages: Sequence[ContextMessage]
    use_captions: bool

    def messages(self, max_quality: ImageQuality = ImageQuality.FULL) -> list[AgentMessage]:
        """Returns the built chat context with images limited to a certain quality level."""
        return [
            {
                "role": "user", 
                "content": [
                    utils.make_text_content(msg.text),
                    *utils.build_image_contents(msg, max_quality, self.use_captions),
                ]
            } for msg in reversed(self._messages)
        ]


class ContextBuilder:
    def __init__(self, cog: AgentCogBase):
        self.bot = cog.bot
        self.config = cog.config
        self.session = cog.session
        self.execute_captioner = cog.execute_captioner
        self.is_busy = cog.is_busy
        self.image_cache: dict[tuple, ImageEntry]      = ExpiringDict(constants.CACHE_SIZE, constants.CACHE_AGE)
        self.text_cache: dict[int, str]                = ExpiringDict(constants.CACHE_SIZE, constants.CACHE_AGE)
        self.message_cache: dict[int, discord.Message] = ExpiringDict(constants.CACHE_SIZE, constants.CACHE_AGE)

    async def build_context(
        self,
        ctx: commands.Context,
        backread: list[discord.Message],
        config: AgentCogGuildConfig,
        result: CompletionResult,
        encoding: tiktoken.Encoding,
    ) -> AgentChatContext:
        return await ContextBuilderPipeline(self, ctx, backread, config, result, encoding).build()

    async def fetch_message(self, channel: object, message_id: int) -> discord.Message | None:
        if message := self.message_cache.get(message_id):
            return await utils.resolve_components(message)
        if not isinstance(channel, discord.abc.Messageable):
            return None
        try:
            message = await channel.fetch_message(message_id)
        except discord.DiscordException:
            return None
        self.message_cache[message_id] = message
        return await utils.resolve_components(message)


class ContextBuilderPipeline:
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
        self.config = config
        self.result = result
        self.encoding = encoding
        self.imagescanner = utils.get_cog(builder.bot, ImageScanner)
        self.in_context = {backmsg.id: backmsg for backmsg in backread}
        self.quotes: dict[int, discord.Message] = {}
        self.generated: dict[int, dict[str, str]] = {}
        low, high = utils.image_tokens(config.model_responder.value)
        self.image_tokens = {ImageQuality.NONE: 0, ImageQuality.LOW: low, ImageQuality.FULL: high}

    async def build(self) -> AgentChatContext:
        self.backread = list(await asyncio.gather(*(utils.resolve_components(message) for message in self.backread)))
        self.in_context = {message.id: message for message in self.backread}
        self.quotes = await self.resolve_quotes()
        self.generated = await self.resolve_generated()
        messages = await self.parse_messages()
        self.plan_images(messages)
        messages = self.trim(messages)
        await self.resolve_images(messages)
        self.report(messages)
        return AgentChatContext(messages, self.config.use_captions.value)


    # Fetching

    async def resolve_quotes(self) -> dict[int, discord.Message]:
        """Returns the quoted message of each message."""
        older_ids = [backmsg.id for backmsg in self.backread[1:]]
        quote_ids = {
            backmsg.id: ref.message_id
            for backmsg, older_id in zip_longest(self.backread, older_ids)
            if (ref := backmsg.reference) and ref.message_id and ref.message_id != older_id
        }
        replies = {quote_id: backmsg for backmsg in self.backread if (quote_id := quote_ids.get(backmsg.id))}  # fetch each once
        fetched = await utils.gather_logged(log, (self.fetch_quote(backmsg, quote_id) for quote_id, backmsg in replies.items()))
        found = dict(zip(replies, fetched))
        return {message_id: quote for message_id, quote_id in quote_ids.items() if (quote := found[quote_id])}


    async def fetch_quote(self, backmsg: discord.Message, quote_id: int) -> discord.Message | None:
        cached = backmsg.reference.cached_message if backmsg.reference else None
        quote = self.in_context.get(quote_id) or cached or await self.builder.fetch_message(backmsg.channel, quote_id)
        return await utils.resolve_components(quote) if quote else None


    async def resolve_generated(self) -> dict[int, dict[str, str]]:
        """Returns the metadata of the generated image of each message."""
        messages = {message.id: message for message in [*self.backread, *self.quotes.values()]}
        found = await utils.gather_logged(log, (self.detect_generated(message) for message in messages.values()))
        return {message_id: generated for message_id, generated in zip(messages, found) if generated}


    async def detect_generated(self, message: discord.Message) -> dict[str, str] | None:
        if message.author != self.ctx.me or len(message.attachments) != 1:
            return None
        if "gptimage" in message.attachments[0].filename:
            return {"type": "gptimage"}
        if self.imagescanner:
            return await self.imagescanner.grab_metadata_dict(message)
        return None


    # Parsing

    async def parse_messages(self) -> list[ContextMessage]:
        parsed = await utils.gather_logged(log, (self.parse_message(backmsg) for backmsg in self.backread))
        return [msg or self.failed_message(backmsg) for msg, backmsg in zip(parsed, self.backread)]


    async def parse_message(self, backmsg: discord.Message) -> ContextMessage:
        obj, inlines = await self.parse_discord_message(backmsg, exhaustive=True, recursive=True)
        return self.make_message(backmsg, obj, inlines)


    def failed_message(self, backmsg: discord.Message) -> ContextMessage:
        obj = {**self.message_header(backmsg), "error": "Failed to process this message"}
        return self.make_message(backmsg, {"chat_message": obj}, {})


    def make_message(self, backmsg: discord.Message, obj: StructuredObject, inlines: dict[str, StructuredObject]) -> ContextMessage:
        text = xmltodict.unparse(obj, full_document=False)
        for before, after_obj in inlines.items():
            text = text.replace(utils.escape_xml(before), xmltodict.unparse(after_obj, full_document=False))
        text = utils.resolve_omitted(text)
        return ContextMessage(backmsg, text, len(self.encoding.encode(text)))


    # Planning

    def plan_images(self, messages: list[ContextMessage]) -> None:
        """
        Decides the quality tier of each image according to configured budgets, newest first.
        An image is listed in the newest message that shows it, either by itself or by quoting it.
        """
        budget_full, budget_low = self.config.max_images.value, self.config.max_images_low.value
        tiers = chain(repeat(ImageQuality.FULL, budget_full), repeat(ImageQuality.LOW, budget_low), repeat(ImageQuality.NONE))
        placed: set[int] = set()
        for msg in messages:
            for shown in (msg.message, self.quotes.get(msg.message.id)):
                if not shown or shown.id in placed:
                    continue
                placed.add(shown.id)
                msg.slots.extend(ImageSlot(src, next(tiers)) for src in self.image_sources(shown))


    def image_sources(self, message: discord.Message) -> list[ImageSource]:
        if (generated := self.generated.get(message.id)) and generated.get("Prompt"):
            return []  # the prompt is sent instead
        return utils.image_sources(message)[:constants.MAX_IMAGES_PER_MESSAGE]


    def tokens(self, msg: ContextMessage) -> int:
        return msg.text_tokens + sum(self.image_tokens[slot.quality] for slot in msg.slots)


    def trim(self, messages: list[ContextMessage]) -> list[ContextMessage]:
        """Keeps messages until the token budget runs out, including the one that crosses it."""
        used_before = accumulate((self.tokens(msg) for msg in messages), initial=0)
        return [msg for msg, used in zip(messages, used_before) if used < self.config.backread_tokens.value]


    # Result

    def report(self, messages: list[ContextMessage]) -> None:
        tiers = Counter(slot.quality for msg in messages for slot in msg.slots)
        self.result.messages = len(messages)
        self.result.images = tiers[ImageQuality.FULL]
        self.result.images_low = tiers[ImageQuality.LOW]
        self.result.tokens.backread = sum(self.tokens(msg) for msg in messages)


    # Images

    async def resolve_images(self, messages: list[ContextMessage]) -> None:
        slots = [slot for msg in messages for slot in msg.slots if slot.quality]
        entries = await utils.gather_logged(log, (self.resolve_image(slot) for slot in slots))
        for slot, entry in zip(slots, entries):
            if entry and entry.image_full:
                slot.entry = entry
            else:
                slot.quality = ImageQuality.NONE


    async def resolve_image(self, slot: ImageSlot) -> ImageEntry:
        full_pixels = self.config.max_image_resolution.value ** 2
        low_pixels = self.config.max_caption_resolution.value ** 2
        entry = self.builder.image_cache.setdefault((slot.src.key, full_pixels, low_pixels), ImageEntry())
        async with entry.lock:
            entry.image_full = entry.image_full or await self.fetch_image(slot.src, full_pixels)
            if entry.image_full:
                entry.image_low = entry.image_low or await asyncio.to_thread(utils.normalize_image, entry.image_full, low_pixels)
            if entry.image_low and self.config.use_captions.value and not entry.caption:
                content = utils.make_image_content(entry.image_low, low_detail=True)
                try:
                    entry.caption = await self.builder.execute_captioner(self.ctx, content, self.result)
                except Exception as error:
                    log.warning(f"failed to make caption for {slot.src.url} - {type(error).__name__}: {error}")
        return entry


    async def fetch_image(self, src: ImageSource, max_pixels: int) -> bytes | None:
        try:
            image = self.scanned_image(src) or await utils.read_image_source(src, self.builder.session)
            return await asyncio.to_thread(utils.normalize_image, image, max_pixels)
        except Exception as error:
            log.warning(f"failed to get image data for {src.url}: {type(error).__name__}: {error}")
            return None


    def scanned_image(self, src: ImageSource) -> bytes | None:
        if self.imagescanner and src.attachment:
            return self.imagescanner.image_cache.get(src.message_id, ({}, {}))[1].get(src.att_index)
        return None


    # Messages

    def message_header(self, message: discord.Message) -> StructuredObject:
        obj = {
            "@time": message.created_at.astimezone().strftime(constants.DATETIME_FORMATTING),
            "@username": message.author.name,
        }
        if isinstance(message.author, discord.Member) and message.author.nick:
            obj["@nickname"] = message.author.nick
        return obj


    def clean_content(self, text: str) -> str:
        text = utils.resolve_emotes(self.builder.bot, text)
        return utils.resolve_mentions(self.builder.bot, self.ctx.guild, text)


    async def parse_discord_message(
        self,
        message: discord.Message,
        exhaustive: bool,
        recursive: bool,
    ) -> tuple[StructuredObject, dict[str, StructuredObject]]:
        """
        Converts a message into a dictionary of structured information that may then be unparsed into xml.
        Also returns a dictionary of inline objects to be injected back into the final string.
        """
        assert message.guild
        generated = self.generated.get(message.id) or {}
        prompt = generated.get("Prompt")
        recursive = recursive and not generated  # a generated image doesn't need the messages around it
        inlines: dict[str, StructuredObject] = {}
        obj = self.message_header(message)
        starting_len = len(obj)
        if message.id != self.backread[0].id and self.builder.is_busy(message.id):
            return ({"chat_message": {**obj, "error": "This message is currently being processed"}}, inlines)
        # generated image
        if prompt:
            obj["generated_image"] = {
                "@filename": message.attachments[0].filename,
                "@dimensions": generated.get("Size", "unknown"),
                "prompt": utils.parse_prompt(prompt),
            }
        # quote
        if recursive and (quote := self.quotes.get(message.id)):
            quote_obj, quote_inlines = await self.parse_discord_message(quote, exhaustive=quote.id not in self.in_context, recursive=False)
            obj["quote"] = quote_obj
            inlines.update(quote_inlines)
        # text
        text_obj, text_inlines = await self.parse_text(message, exhaustive, recursive)
        obj.update(text_obj)
        inlines.update(text_inlines)
        # groups
        if not prompt and (attachments := await self.attachment_objs(message, exhaustive)):
            utils.add_xml_group(obj, attachments, "attachments")
        if stickers := [{"#text": sticker.name} for sticker in message.stickers]:
            utils.add_xml_group(obj, stickers, "stickers")
        if (embeds := self.embed_objs(message, obj.get("content", ""), exhaustive)):
            utils.add_xml_group(obj, embeds, "embeds")
        if components := self.component_objs(message, exhaustive):
            utils.add_xml_group(obj, components, "components")
        if exhaustive and not generated and (buttons := self.button_objs(message)):
            utils.add_xml_group(obj, buttons, "buttons")
        if message.poll:
            obj["poll"] = self.poll_obj(message.poll, exhaustive)
        if len(obj) == starting_len:
            obj["error"] = "Message empty or not supported"
        if exhaustive and (reactions := self.reaction_objs(message)):
            utils.add_xml_group(obj, reactions, "reactions")
        # done
        return {"chat_message": obj}, inlines


    async def parse_text(
        self,
        message: discord.Message,
        exhaustive: bool,
        recursive: bool,
    ) -> tuple[StructuredObject, dict[str, StructuredObject]]:
        """Returns the text fields of a message, and the inline objects that replace its message links."""
        assert message.guild
        if message.is_system():
            return {"action": "Joined the server" if message.type == discord.MessageType.new_member else message.system_content}, {}
        if not message.content:
            return {}, {}
        content = message.content
        for mentioned in message.mentions + message.role_mentions:
            content = content.replace(mentioned.mention, f"@{mentioned.name}")
        for mentioned_ch in message.channel_mentions:
            content = content.replace(mentioned_ch.mention, f"#{mentioned_ch.name}")
        content = self.clean_content(content)
        fields: StructuredObject = {}
        inlines: dict[str, StructuredObject] = {}
        for i, message_link in enumerate(constants.DISCORD_MESSAGE_LINK_PATTERN.finditer(content)):
            guild_id, channel_id, message_id = [int(num) for num in message_link.groups()]
            link_obj = {}
            if message.guild.id != guild_id:
                link_obj["@source"] = "Outside this server"
            elif message.channel.id != channel_id:
                channel = message.guild.get_channel_or_thread(channel_id)
                link_obj["@channel"] = f"#{channel.name}" if channel else "unknown"
            inlines[message_link.group(0)] = {"message_link": {"#text": utils.make_omitted(), **link_obj}}
            # add the linked message if it is the first link
            if i == 0 and recursive:
                guild = self.builder.bot.get_guild(guild_id)
                channel = guild.get_channel_or_thread(channel_id) if guild else None
                if linked := await self.builder.fetch_message(channel, message_id):
                    linked_obj, linked_inlines = await self.parse_discord_message(linked, exhaustive=linked.id not in self.in_context, recursive=False)
                    fields["linked_message"] = {**link_obj, **linked_obj}
                    inlines.update(linked_inlines)
        if not exhaustive:
            content = utils.truncate_with_omitted(content, self.config.max_quote.value)
        return {**fields, "content": content}, inlines


    async def attachment_objs(self, message: discord.Message, exhaustive: bool) -> list[StructuredObject]:
        remaining = self.config.max_text_file.value
        attachments = []
        for attachment in message.attachments:
            obj = {"@filename": attachment.filename}
            if exhaustive and remaining > 0 and (attachment.content_type or "").startswith("text"):
                if content := await self.read_text_file(attachment):
                    obj["content"] = utils.truncate_with_omitted(content, remaining, keep_ends=True)
                    remaining -= len(obj["content"])
            attachments.append(obj)
        return attachments


    def embed_objs(self, message: discord.Message, content: str, exhaustive: bool) -> list[StructuredObject]:
        embeds = []
        for embed in message.embeds:
            obj = {}
            if embed.author:
                obj["header"] = embed.author.name
            if embed.title:
                obj["title"] = self.clean_content(embed.title)
            if embed.description:
                obj["description"] = self.clean_content(embed.description)
            if embed.url and embed.url[:self.config.max_quote.value] not in content:
                obj["url"] = embed.url
            if embed.image and embed.image.url:
                obj["image"] = embed.image.url
            if embed.thumbnail and embed.thumbnail.url:
                obj["thumbnail"] = embed.thumbnail.url
            if embed.fields and exhaustive:
                fields = [{
                    "@name": self.clean_content(field.name or ""),
                    "#text": self.clean_content(field.value or "")
                } for field in embed.fields]
                utils.add_xml_group(obj, fields, "fields")
            if embed.footer:
                obj["footer"] = embed.footer.text
            if obj:
                embeds.append(obj)
        return embeds


    def button_objs(self, message: discord.Message) -> list[StructuredObject]:
        return [
            {"#text": utils.button_label(button)}
            for button in utils.walk_components(message.components) if isinstance(button, discord.Button)
        ]


    def component_objs(self, message: discord.Message, exhaustive: bool) -> list[StructuredObject]:
        objects: list[StructuredObject] = []
        remaining = self.config.backread_tokens.value * 4 if exhaustive else self.config.max_quote.value
        for component in utils.walk_components(message.components):
            if isinstance(component, discord.TextDisplay):
                text = self.clean_content(component.content)
                if remaining > 0:
                    objects.append({"text": utils.truncate_with_omitted(text, remaining)})
                    remaining -= len(text)
            elif isinstance(component, discord.Button) and component.url:
                objects.append({"link": component.url})
            for media, description in utils.component_media(component):
                kind = utils.media_kind(media)
                obj: StructuredObject = {kind: media.url}
                if description and remaining > 0:
                    obj["description"] = utils.truncate_with_omitted(self.clean_content(description), remaining)
                    remaining -= len(description)
                objects.append(obj)
        return objects


    def poll_obj(self, poll: discord.Poll, exhaustive: bool) -> StructuredObject:
        obj = {
            "@finished": str(poll.is_finalised()).lower(),
            "question": poll.question
        }
        if exhaustive:
            answers = [{
                "@votes": str(answer.vote_count),
                "#text": answer.text
            } for answer in poll.answers]
            utils.add_xml_group(obj, answers, "answers")
        elif poll.victor_answer:
            obj["winner"] = poll.victor_answer.text
        return obj


    def reaction_objs(self, message: discord.Message) -> list[StructuredObject]:
        return [{
            "@count": str(reaction.count),
            "@me": str(reaction.me).lower(),
            "#text": reaction.emoji if isinstance(reaction.emoji, str) else f":{reaction.emoji.name}:",
        } for reaction in message.reactions[:5] if reaction.emoji != "🔎"]


    async def read_text_file(self, attachment: discord.Attachment) -> str | None:
        file_content = self.builder.text_cache.get(attachment.id)
        if file_content is not None:
            return file_content
        if attachment.size > constants.TEXT_DOWNLOAD_MAXSIZE:
            return None
        try:
            file_content = (await attachment.read()).decode()
        except (discord.DiscordException, UnicodeDecodeError) as error:
            log.warning(f"Processing text attachment {attachment.filename}: {type(error).__name__}: {error}")
            return None
        self.builder.text_cache[attachment.id] = file_content
        return file_content
