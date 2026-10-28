import io
import aiohttp
import discord
from io import BytesIO
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
from PIL import Image, ImageDraw, UnidentifiedImageError
from redbot.core import commands

from agent.constants import IMAGE_EXTENSIONS, MEDIA_HEADERS, URL_PATTERN
from agent.types import ImageSource
from .misc_utils import get_filename
from .string_utils import undo_xml


def image_sources(message: discord.Message) -> list[ImageSource]:
    """Collect images in display order, retaining original and proxy URLs."""
    candidates = [
        ImageSource(message.id, att, i, att.url, att.proxy_url)
        for i, att in enumerate(message.attachments)
        if att.content_type and att.content_type.startswith("image/")
    ]
    for embed in message.embeds:
        media = embed.image if embed.image.url else embed.thumbnail
        if media.url:
            candidates.append(ImageSource(message.id, url=media.url, proxy_url=media.proxy_url))
    for match in URL_PATTERN.findall(message.content or ""):
        url = match.rstrip(">)]|")
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        formats = parse_qs(parsed.query).get("format", [])
        if parsed.path.lower().endswith(IMAGE_EXTENSIONS) or any(
            f".{fmt.lower()}" in IMAGE_EXTENSIONS for fmt in formats
        ):
            candidates.append(ImageSource(message.id, url=url))
    result: list[ImageSource] = []
    seen: set[str] = set()
    for source in candidates:
        urls = {url for url in (source.url, source.proxy_url) if url}
        if not urls & seen:
            result.append(source)
        seen.update(urls)
    return result


async def image_messages(ctx: commands.Context, limit: int) -> list[discord.Message]:
    """Keep the enriched triggering message and prefer its explicit reply target."""
    messages = [ctx.message]
    ref = ctx.message.reference
    if ref and ref.message_id and ref.channel_id == ctx.channel.id:
        try:
            quoted = ref.cached_message or await ctx.channel.fetch_message(ref.message_id)
            messages.insert(0, quoted)
        except discord.DiscordException:
            pass
    messages.extend([message async for message in ctx.channel.history(limit=limit, before=ctx.message)])
    return messages


def find_image_source(reference: str, messages: list[discord.Message], consumed: list[ImageSource]) -> ImageSource | None:
    reference = undo_xml(reference).strip().strip("<>")
    used = {url for source in consumed for url in (source.url, source.proxy_url) if url}
    sources = [source for message in messages for source in image_sources(message)
               if not used.intersection((source.url, source.proxy_url))]
    for source in sources:
        if reference in (source.url, source.proxy_url):
            if reference == source.proxy_url and not source.attachment:
                return replace(source, url=reference, proxy_url=source.url)
            return source
    for source in sources:
        filename = source.attachment.filename if source.attachment else get_filename(source.url or "")
        if reference == filename:
            return source
    return None


async def read_image_source(source: ImageSource, session: aiohttp.ClientSession, max_bytes: int = 20 * 1024 * 1024) -> bytes:
    data: bytes | bytearray
    if source.attachment:
        if source.attachment.size > max_bytes:
            raise ValueError("Image exceeds the download limit")
        data = await source.attachment.read()
    elif source.url:
        async with session.get(source.url, headers=MEDIA_HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError("Image exceeds the download limit")
    else:
        raise ValueError("Image has no downloadable source")
    if len(data) > max_bytes:
        raise ValueError("Image exceeds the download limit")
    return bytes(data)


def find_nearest_resolution(current: tuple[int, int], targets: list[tuple[int, int]]) -> tuple[int, int]:
    ratio = current[0] / current[1]
    best_match = min(targets, key=lambda res: abs((res[0] / res[1]) - ratio))
    return best_match

def scale_to_size(width: int, height: int, pixels: int) -> tuple[int, int]:
    scale = (pixels / (width * height)) ** 0.5
    return int(width * scale), int(height * scale)

def normalize_image(b: bytes | BytesIO, max_pixels: int | None = None, thumbnail_size: int | None = None) -> bytes | None:
    b = b if isinstance(b, BytesIO) else BytesIO(b)
    b.seek(0)
    try:
        image = Image.open(b)
    except (UnidentifiedImageError, OSError):
        return None
    if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
        image_rgba = image.convert("RGBA")
        background = Image.new("RGB", image.size, (0, 0, 0))
        background.paste(image_rgba, (0, 0), image_rgba.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    format = "PNG"
    if max_pixels and image.width*image.height > max_pixels:
        width, height = scale_to_size(image.width, image.height, max_pixels)
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    if thumbnail_size:
        format = "JPEG"
        image.thumbnail((thumbnail_size, thumbnail_size), Image.Resampling.LANCZOS)
    fp = BytesIO()
    image.save(fp, format, quality=90)
    return fp.getvalue()

def make_circle(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, *img.size), fill=255)
    img.putalpha(mask)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
