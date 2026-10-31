import asyncio
import trafilatura
from typing import Awaitable, Iterable, TypeVar
from logging import Logger
from datetime import datetime

from .string_utils import add_xml_group
from agent.types import StructuredObject
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
