import os
import struct
import asyncio
import mutagen
import trafilatura
from io import BytesIO
from base64 import b64encode
from datetime import datetime
from urllib.parse import urlparse

from agent.schema import StructuredObject
from agent.constants import DATETIME_FORMATTING


def get_filename(url: str) -> str:
    return os.path.basename(urlparse(url).path)


def parse_arcenciel_model(data: dict, *, description_limit: int | None = None,
                         trigger_limit: int | None = None) -> StructuredObject:
    obj = {
        "@title": data["title"],
        "@type": data["type"],
        "url": f"https://arcenciel.io/models/{data['id']}",
        "uploader": data["uploader"]["username"],
        "description": trafilatura.extract(data["description"]) or data["description"] or "(Empty)",
    }
    versions_obj = []
    versions: list[dict] = sorted(data.get("versions", []), key=lambda v: v['id'], reverse=True)
    for i, version in enumerate(versions):
        ver_obj = {
            "@name": version["versionName"],
            "@base_model": version["baseModel"],
        }
        if description_limit is None and isinstance(version.get("publishedAt"), str):
            ver_obj["@published"] = datetime.fromisoformat(version["publishedAt"]).astimezone().strftime(DATETIME_FORMATTING)
        filename = str(version.get("filePath", "")).split("/")[-1].replace(".safetensors", "").strip()
        if not filename:
            ver_obj["filename"] = "(unknown)"
        elif data["type"] == "LORA":
            ver_obj["lora"] = f"<lora:{filename}:1.0>"
        else:
            ver_obj["filename"] = filename
        if i <= 1:
            if description_limit is None and "aboutThisVersion" in version:
                ver_obj["description"] = version["aboutThisVersion"]
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
                ver_obj["activation_tags"] = {"prompt": tags_obj}
                if trigger_limit is not None:
                    remaining = trigger_limit
                    selected = []
                    for tag in tags_obj:
                        if len(tag["#text"]) <= remaining:
                            selected.append(tag)
                            remaining -= len(tag["#text"])
                    ver_obj["activation_tags"] = {"prompt": selected}
                    if len(selected) < len(tags_obj):
                        ver_obj["activation_tags"]["@more"] = True
        elif description_limit is None:
            ver_obj["#text"] = "..."
        versions_obj.append(ver_obj)
    if description_limit == 0:
        obj.pop("description")
    elif description_limit is not None and len(obj["description"]) > description_limit:
        obj["description"] = obj["description"][:description_limit] + "..."
    obj["versions"] = {"version": versions_obj}
    return obj


async def generate_waveform(audio_bytes: bytes, max_points=256, sample_rate=8000) -> tuple[str, float]:
    audio = mutagen.File(BytesIO(audio_bytes))
    if audio is None or audio.info is None or not hasattr(audio.info, "length"):
        raise ValueError("Could not read audio metadata")
    duration = getattr(audio.info, "length")

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

    point_count = max(1, min(int(duration * 10), max_points))
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

    return b64encode(waveform_bytes).decode("utf-8"), duration
