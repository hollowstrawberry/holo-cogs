import logging
import asyncio
import discord
from copy import copy
from typing import TYPE_CHECKING, Iterable, Iterator, cast
from urllib.parse import urlsplit
from expiringdict import ExpiringDict
from discord.components import _component_factory

if TYPE_CHECKING:
    from discord.types.components import Component as ComponentPayload

from agent import constants

log = logging.getLogger("agent.components")
COMPONENT_CACHE: dict[tuple, list[discord.Component]] = ExpiringDict(constants.CACHE_SIZE, constants.CACHE_AGE)


async def resolve_components(message: discord.Message) -> discord.Message:
    """
    Temporary solution to discord.py's lack of component-embeds.
    Feature specs: https://docs.discord.com/developers/link-previews/component-embeds
    Feature status: https://github.com/Rapptz/discord.py/pull/10528
    """
    embeds = [embed for embed in message.embeds if embed.type == "components"]
    if not embeds:
        return message
    key = (message.channel.id, message.id, message.edited_at, tuple(embed.url for embed in embeds))
    components = COMPONENT_CACHE.get(key)
    if components is None:
        components = []
        try:
            # Keep discord.py's HTTP rate limiting and component decoding at this compatibility boundary.
            data = await asyncio.wait_for(
                message._state.http.get_message(message.channel.id, message.id),
                timeout=constants.IMAGE_DOWNLOAD_TIMEOUT,
            )
            for embed in data.get("embeds", []):
                for payload in cast("list[ComponentPayload]", embed.get("components", [])):
                    if component := _component_factory(payload):
                        components.append(component)
        except Exception as error:
            log.warning(f"Could not read components for {message.id} - {type(error).__name__}: {error}")
        COMPONENT_CACHE[key] = components
    if not components:
        return message
    result = copy(message)
    result.components = [*components, *message.components]
    # The recovered content now lives in components; retain its source link without fetching it twice.
    result.embeds = [discord.Embed(url=embed.url) if embed.type == "components" else embed for embed in message.embeds]
    return result


def walk_components(components: Iterable[discord.Component]) -> Iterator[discord.Component]:
    for component in components:
        yield component
        if isinstance(component, (discord.Container, discord.ActionRow, discord.SectionComponent)):
            yield from walk_components(component.children)
        if isinstance(component, discord.SectionComponent):
            yield component.accessory


def component_media(component: discord.Component) -> Iterator[tuple[discord.UnfurledMediaItem, str | None]]:
    if isinstance(component, discord.MediaGalleryComponent):
        for item in component.items:
            yield item.media, item.description
    elif isinstance(component, discord.ThumbnailComponent):
        yield component.media, component.description
    elif isinstance(component, discord.FileComponent):
        yield component.media, component.name


def media_kind(media: discord.UnfurledMediaItem) -> str:
    if media.content_type:
        kind = media.content_type.split("/", 1)[0]
        return kind if kind in ("image", "video") else "file"
    # Discord may not have populated the media metadata yet.
    path = urlsplit(media.url).path.lower()
    if path.endswith(constants.IMAGE_EXTENSIONS):
        return "image"
    if path.endswith((".mp4", ".webm", ".mov")):
        return "video"
    return "file"
