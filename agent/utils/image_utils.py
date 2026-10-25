import io
from io import BytesIO
from PIL import Image, ImageDraw, UnidentifiedImageError


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
