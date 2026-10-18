import asyncio
import logging
import re

import aiohttp

from agent.schema import ToolCall, Function, Parameters
from agent.tools.base import ToolBase


log = logging.getLogger("agent.arcenciel_images")
ADULT_RATINGS = {"sensitive", "explicit"}
IMAGE_MARKDOWN_LINK = re.compile(
    r"(?<!\|)(?:\|\|)?\[([^\]\n]+)\]\((?:\|\|)?<?https://arcenciel\.io/images/([1-9]\d*)>?(?:\|\|)?\)(?:\|\|)?"
)
IMAGE_ANGLE_LINK = re.compile(r"(?<!\|)<https://arcenciel\.io/images/([1-9]\d*)>(?!\|)")
IMAGE_URL = re.compile(r"(?<![\|<])https://arcenciel\.io/images/([1-9]\d*)(?!\d|\|)")


def channel_is_nsfw(channel) -> bool:
    return bool(
        getattr(channel, "nsfw", False)
        or getattr(getattr(channel, "parent", None), "nsfw", False)
    )


def spoiler_adult_image_links(text: str, image_ids: set[int]) -> str:
    """Keep links and their Discord-generated previews hidden if the model reformats them."""
    if not image_ids:
        return text

    def replace_markdown(match: re.Match[str]) -> str:
        image_id = int(match.group(2))
        if image_id not in image_ids:
            return match.group(0)
        return f"{match.group(1)}: ||https://arcenciel.io/images/{image_id}||"

    text = IMAGE_MARKDOWN_LINK.sub(replace_markdown, text)
    text = IMAGE_ANGLE_LINK.sub(
        lambda match: (
            f"||https://arcenciel.io/images/{match.group(1)}||"
            if int(match.group(1)) in image_ids
            else match.group(0)
        ),
        text,
    )
    return IMAGE_URL.sub(
        lambda match: (
            f"||{match.group(0)}||"
            if int(match.group(1)) in image_ids
            else match.group(0)
        ),
        text,
    )


class ArcencielImagesTool(ToolBase):
    display_name = "arcenciel_images"
    schema = ToolCall(
        Function(
            name="search_images_arcenciel",
            description="Search published image posts on Arc en Ciel. Use this for images, not models or LoRAs.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string",
                        "description": "Search image titles, descriptions, prompts, and tags. Leave empty to list recent images.",
                    },
                    "user": {
                        "type": "string",
                        "description": "Optional exact Arc en Ciel username of the image uploader.",
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["newest", "oldest", "reactions", "hot"],
                        "description": "Image order; defaults to newest.",
                    },
                    "page": {
                        "type": "integer",
                        "description": "Page number, starting at 1.",
                    },
                },
                required=[],
            ),
        )
    )

    async def run(self, arguments: dict) -> dict | str:
        arcenciel = self.ctx.bot.get_cog("Arcenciel")
        api = getattr(arcenciel, "api", None)
        if api is None or not api.session.headers.get("x-api-key"):
            return "<error>Arc en Ciel image search is not configured</error>"

        query = str(arguments.get("query") or "").strip()[:200]
        user = str(arguments.get("user") or "").strip()[:100]
        sort = arguments.get("sort")
        if sort not in ("newest", "oldest", "reactions", "hot"):
            sort = "newest"
        try:
            page = max(1, min(int(arguments.get("page") or 1), 100))
        except (TypeError, ValueError):
            page = 1

        ratings = {"safe", "questionable"}
        if channel_is_nsfw(self.ctx.channel):
            ratings.update(("sensitive", "explicit"))
        params = {
            "search": query,
            "sort": sort,
            "page": page,
            "limit": 5,
            "ratings": ",".join(sorted(ratings)),
        }
        timeout = aiohttp.ClientTimeout(total=10)

        try:
            if user:
                async with api.session.get(
                    f"{api.endpoint}/users/search", params={"q": user}, timeout=timeout
                ) as response:
                    response.raise_for_status()
                    users = await response.json()
                match = next(
                    (
                        item
                        for item in users
                        if item.get("username", "").casefold() == user.casefold()
                    ),
                    None,
                )
                if match is None:
                    return "No Arc en Ciel user with that exact username"
                params["userId"] = match["id"]

            async with api.session.get(
                f"{api.endpoint}/images/search", params=params, timeout=timeout
            ) as response:
                response.raise_for_status()
                data = await response.json()
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
            TypeError,
        ) as error:
            log.warning(
                "Searching Arc en Ciel images: %s: %s", type(error).__name__, error
            )
            return "<error>Failed to search Arc en Ciel images</error>"

        results = []
        for image in data.get("data", []):
            if image.get("rating") not in ratings or image.get("status") != "PUBLISHED":
                continue
            image_id = image.get("id")
            if type(image_id) is not int or image_id <= 0:
                continue
            uploader = image.get("uploader") or {}
            url = f"https://arcenciel.io/images/{image_id}"
            results.append(
                {
                    "@id": image_id,
                    "@title": (image.get("title") or f"Image #{image_id}")[:120],
                    "url": f"||{url}||" if image["rating"] in ADULT_RATINGS else url,
                    "uploader": str(uploader.get("username") or "Unknown")[:80],
                    "rating": image["rating"],
                }
            )

        if not results:
            return "No published images found on this page"
        return {"results": {"image": results}}
