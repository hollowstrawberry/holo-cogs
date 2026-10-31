import io
import asyncio
import aiohttp
import discord
from io import BytesIO
from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from PIL import Image, ImageDraw, UnidentifiedImageError
from expiringdict import ExpiringDict

from .string_utils import undo_xml, get_filename
from .component_utils import walk_components, component_media, media_kind
from agent.types import ImageSource, ImageEntry
from agent.constants import (
    IMAGE_DOWNLOAD_MAXSIZE, IMAGE_DOWNLOAD_TIMEOUT, IMAGE_EXTENSIONS, MEDIA_HEADERS, URL_PATTERN,
    CACHE_SIZE, VIDEO_FRAME_MAXSIZE, VIDEO_FRAME_TIMEOUT,
)

VIDEO_FRAMES: dict[int | str, ImageEntry] = ExpiringDict(CACHE_SIZE, 5 * 60)
VIDEO_DECODERS = asyncio.Semaphore(2)


def image_sources(message: discord.Message) -> list[ImageSource]:
    """Collect images in display order, retaining original and proxy URLs."""
    candidates = [
        ImageSource(att.url, message.id, att, i, att.proxy_url)
        for i, att in enumerate(message.attachments)
        if att.content_type and att.content_type.startswith("image/")
    ]
    for embed in message.embeds:
        embed_media = embed.image if embed.image.url else embed.thumbnail
        if embed_media.url:
            candidates.append(ImageSource(embed_media.url, message.id, proxy_url=embed_media.proxy_url))
    # Gallery content takes priority over section thumbnails, which are often profile pictures.
    components = sorted(walk_components(message.components), key=lambda item: isinstance(item, discord.ThumbnailComponent))
    for component in components:
        for media, _ in component_media(component):
            kind = media_kind(media)
            if kind in ("image", "video"):
                attachment = next((
                    (i, att) for i, att in enumerate(message.attachments)
                    if att.id == media.attachment_id or media.url == f"attachment://{att.filename}"
                ), None)
                if attachment:
                    index, att = attachment
                    candidates.append(ImageSource(att.url, message.id, att, index, att.proxy_url, kind == "video"))
                else:
                    candidates.append(ImageSource(
                        media.url, message.id, proxy_url=media.proxy_url, is_video=kind == "video",
                    ))
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
            candidates.append(ImageSource(url, message.id))
    result: list[ImageSource] = []
    seen: set[str] = set()
    for source in candidates:
        urls = {url for url in (source.url, source.proxy_url) if url}
        if not urls & seen:
            result.append(source)
        seen.update(urls)
    return result

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

async def read_image_source(source: ImageSource, session: aiohttp.ClientSession, max_bytes: int = IMAGE_DOWNLOAD_MAXSIZE) -> bytes:
    if not source.is_video:
        return await _read_media_source(source, session, max_bytes)
    entry = VIDEO_FRAMES.setdefault(source.key, ImageEntry())
    async with entry.lock:
        if entry.image_full is None:
            try:
                async with VIDEO_DECODERS:
                    data = await _read_media_source(source, session, max_bytes)
                    entry.image_full = await video_frame(data)
            except Exception:
                entry.image_full = b""  # Avoid retrying an unavailable video on every turn.
                raise
        if not entry.image_full:
            raise ValueError("Video preview unavailable")
        return entry.image_full


async def video_frame(data: bytes) -> bytes:
    """Decode one bounded still from a local video; FFmpeg never receives a remote URL."""
    with TemporaryDirectory(prefix="agent-video-") as directory:
        path = Path(directory) / "video"
        await asyncio.to_thread(path.write_bytes, data)
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-v", "error", "-threads", "1",
            "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm",
            "-i", str(path), "-threads", "1", "-frames:v", "1", "-an", "-sn",
            "-vf", f"scale='min({VIDEO_FRAME_MAXSIZE},iw)':'min({VIDEO_FRAME_MAXSIZE},ih)':force_original_aspect_ratio=decrease",
            "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "3", "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            frame, _ = await asyncio.wait_for(process.communicate(), timeout=VIDEO_FRAME_TIMEOUT)
            if process.returncode or not frame:
                raise ValueError("Could not decode a video preview")
            return frame
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def _read_media_source(source: ImageSource, session: aiohttp.ClientSession, max_bytes: int) -> bytes:
    data: bytes | bytearray
    if source.attachment:
        if source.attachment.size > max_bytes:
            raise ValueError("Image exceeds the download limit")
        data = await source.attachment.read()
    elif source.url:
        timeout = aiohttp.ClientTimeout(IMAGE_DOWNLOAD_TIMEOUT)
        async with session.get(source.url, headers=MEDIA_HEADERS, timeout=timeout) as response:
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

def crop_circle(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, *img.size), fill=255)
    img.putalpha(mask)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
