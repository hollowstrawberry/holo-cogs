import re
import logging
import asyncio
import aiohttp

from agent.utils import add_xml_group, parse_arcenciel_image, get_cog, is_nsfw
from agent.schema import ToolCall, Function, Parameters
from agent.tools.base import ToolBase

log = logging.getLogger("agent.arcenciel_images")

SORT_TYPES = ("newest", "oldest", "reactions", "hot")
IMAGE_MARKDOWN_LINK = re.compile(
    r"(?<!\|)(?:\|\|)?\[([^\]\n]+)\]\((?:\|\|)?<?https://arcenciel\.io/images/([1-9]\d*)>?(?:\|\|)?\)(?:\|\|)?"
)
IMAGE_ANGLE_LINK = re.compile(r"(?<!\|)<https://arcenciel\.io/images/([1-9]\d*)>(?!\|)")
IMAGE_URL = re.compile(r"(?<![\|<])https://arcenciel\.io/images/([1-9]\d*)(?!\d|\|)")


def spoiler_nsfw_image_links(text: str, image_ids: list[int]) -> str:
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
    settings = {
        "arcenciel_emoji": "📁",
        "arcenciel_timeout": "10",
        "arcenciel_image_results": "5",
        "arcenciel_prompt_chars": "2000",
    }
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
                        "enum": list(SORT_TYPES),
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
        try:
            from arcenciel.arcenciel import Arcenciel
        except ImportError:
            return "<error>Arc en Ciel cog not installed, please notify the bot owner</error>"
        arcenciel = get_cog(self.cog.bot, Arcenciel)
        if not arcenciel or not arcenciel.api or not arcenciel.api.session.headers.get("x-api-key"):
            return "<error>Arc en Ciel cog not configured, please notify the bot owner</error>"

        query = str(arguments.get("query") or "").strip()[:200]
        user = str(arguments.get("user") or "").strip()[:100]
        sort = arguments.get("sort")

        limit = self.get_setting_int("arcenciel_image_results", 1, 20) or 5
        prompt_limit = self.get_setting_int("arcenciel_prompt_chars", 0, 10000) or 1000
        timeout = asyncio.Timeout(self.get_setting_int("arcenciel_timeout", 1, 1000) or 10)
        emoji = self.get_setting("arcenciel_emoji")

        asyncio.create_task(self.ctx.message.add_reaction(emoji))

        if sort not in SORT_TYPES:
            sort = "newest"
        try:
            page = max(1, min(int(arguments.get("page") or 1), 100))
        except (TypeError, ValueError):
            page = 1
        ratings = {"safe", "questionable"}
        if is_nsfw(self.ctx.channel):
            ratings.update(("sensitive", "explicit"))

        params = {
            "search": query,
            "sort": sort,
            "page": page,
            "limit": limit,
            "ratings": ",".join(sorted(ratings)),
        }

        try:
            if user:
                async with arcenciel.api.session.get(
                    f"{arcenciel.api.endpoint}/users/search",
                    params={"q": user}, headers=self.headers, timeout=timeout
                ) as response:
                    response.raise_for_status()
                    user_data: list[dict] = await response.json()
                found_user = next((item for item in user_data if item.get("username", "").casefold() == user.casefold()), None)
                if found_user is None:
                    return "No Arc en Ciel user with that exact username"
                params["userId"] = found_user["id"]

            async with arcenciel.api.session.get(
                f"{arcenciel.api.endpoint}/images/search",
                params=params, headers=self.headers, timeout=timeout
            ) as response:
                response.raise_for_status()
                data: dict = await response.json()

        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            log.warning(f"Trying to contact Arc en Ciel: {type(error).__name__}: {error}")
            return "<error>Failed to contact Arc en Ciel</error>"

        images: list[dict] = data.get("data", [])
        if not images:
            return "No results"
        
        results = []
        for image in images:
            if image.get("rating") not in ratings or image.get("status") != "PUBLISHED":
                continue
            results.append(parse_arcenciel_image(image, prompt_limit=prompt_limit))

        image_ids: list[int] = [imgid for image in images if (imgid := (image["id"])) and imgid > 0]
        def spoiler_nsfw(text: str) -> str:
            return spoiler_nsfw_image_links(text, image_ids)

        result = {}
        add_xml_group(result, results, "images")
        return {
            "result": result,
            "cleanup": spoiler_nsfw
        }
