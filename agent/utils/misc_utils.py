import os
import struct
import asyncio
import mutagen
import trafilatura
from io import BytesIO
from typing import Awaitable, Iterable, TypeVar
from base64 import b64encode
from logging import Logger
from datetime import datetime
from urllib.parse import urlparse

from .string_utils import add_xml_group
from agent.types import StructuredObject, HasLength
from agent.constants import DATETIME_FORMATTING


T = TypeVar("T")

async def gather_logged(log: Logger, tasks: Iterable[Awaitable[T]]) -> list[T | None]:
    """Like asyncio.gather, but every failure is logged and its result becomes None."""
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for res in results:
        if isinstance(res, BaseException):
            log.warning(f"{type(res).__name__}: {res}", exc_info=res)
    return [None if isinstance(res, BaseException) else res for res in results]


def parse_arcenciel_image(image: dict, prompt_limit: int | None = None) -> StructuredObject:
    image_id = image.get("id")
    uploader = image.get("uploader") or {}
    url = f"https://arcenciel.io/images/{image_id}"
    
    entry = {
        "@id": image_id,
        "@published": datetime.fromisoformat(image["publishedAt"]).astimezone().strftime(DATETIME_FORMATTING),
        "url": f"||{url}||" if image["rating"] in ("sensitive", "explicit") else url,
        "title": (image.get("title") or f"Image #{image_id}")[:120],
        "uploader": str(uploader.get("username") or "Unknown")[:80],
        "rating": image["rating"],
    }
    if image.get("prompt"):
        if prompt_limit is not None and len(image["prompt"]) > prompt_limit:
            image["prompt"] = image["prompt"][:prompt_limit-3] + "..."
        entry["prompt"] = image["prompt"]
    return entry


def parse_arcenciel_model(model: dict, *, description_limit: int | None = None, trigger_limit: int | None = None) -> StructuredObject:
    obj = {
        "@title": model["title"],
        "@type": model["type"],
        "url": f"https://arcenciel.io/models/{model['id']}",
        "uploader": model["uploader"]["username"],
        "description": trafilatura.extract(model["description"]) or model["description"] or "(Empty)",
    }
    versions_obj = []
    versions: list[dict] = sorted(model.get("versions", []), key=lambda v: v['id'], reverse=True)
    for i, version in enumerate(versions):
        ver_obj = {
            "@name": version["versionName"],
            "@base_model": version["baseModel"],
            "@published": datetime.fromisoformat(version["publishedAt"]).astimezone().strftime(DATETIME_FORMATTING)
        }
        filename = str(version.get("filePath", "")).split("/")[-1].replace(".safetensors", "").strip()
        if not filename:
            ver_obj["filename"] = "(unknown)"
        elif model["type"] == "LORA":
            ver_obj["lora"] = f"<lora:{filename}:1.0>"
        else:
            ver_obj["filename"] = filename
        if i < 2:
            if "aboutThisVersion" in version:
                ver_obj["description"] = version["aboutThisVersion"][:description_limit//2 if description_limit else None]
            if "activationTags" in version:
                tags_obj = []
                all_tags: list[str] = version["activationTags"]
                for tags in all_tags:
                    t_obj = {}
                    if tags.count("|") == 1:
                        name, tags = [t.strip() for t in tags.split("|")]
                        t_obj["@name"] = name
                    if len(tags) > 10 and tags in obj["description"]:
                        obj["description"] = obj["description"].replace(tags, "<tags>")
                    t_obj["#text"] = tags
                    tags_obj.append(t_obj)
                if trigger_limit is None:
                    add_xml_group(ver_obj, tags_obj, "prompts")
                else:
                    remaining = trigger_limit
                    selected = []
                    for tag in tags_obj:
                        if len(tag["#text"]) <= remaining:
                            selected.append(tag)
                            remaining -= len(tag["#text"])
                    ver_obj["prompts"] = {"prompt": selected}
                    if len(selected) < len(tags_obj):
                        ver_obj["prompts"]["@more"] = True  # type: ignore
        else:
            ver_obj["#text"] = "..."
        versions_obj.append(ver_obj)

    if description_limit == 0:
        obj.pop("description")
    elif description_limit is not None and len(obj["description"]) > description_limit:
        obj["description"] = obj["description"][:description_limit-3] + "..."
    add_xml_group(obj, versions_obj, "versions")
    return obj


async def generate_waveform(audio_bytes: bytes, max_points=256, sample_rate=8000) -> tuple[str, float]:
    audio = mutagen.File(BytesIO(audio_bytes))
    if not audio or not audio.info or not isinstance(audio.info, HasLength) or not isinstance(audio.info.length, (int, float)):
        raise ValueError("Could not read audio metadata")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", "-",
        "-f", "s16le",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-loglevel", "quiet",
        "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    raw, _ = await proc.communicate(input=audio_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")

    num_samples = len(raw) // 2
    samples = struct.unpack(f"<{num_samples}h", raw[: num_samples * 2])
    abs_samples = [abs(s) for s in samples]

    point_count = max(1, min(int(audio.info.length * 10), max_points))
    points_per_bucket = max(1, len(abs_samples) // point_count)

    buckets = []
    total, count = 0, 0
    for i, v in enumerate(abs_samples):
        total += v
        count += 1
        if (i + 1) % points_per_bucket == 0:
            buckets.append(total // count)
            total, count = 0, 0
    if count:
        buckets.append(total // count)

    highest = max(buckets) if buckets else 1
    mult = 255 / highest if highest else 0
    waveform_bytes = bytes(min(255, int(b * mult)) for b in buckets)

    return b64encode(waveform_bytes).decode(), audio.info.length
