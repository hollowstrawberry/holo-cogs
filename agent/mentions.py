from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Protocol


USER_MENTION_PATTERN = re.compile(r"<@!?(\d{1,22})>")


class _Author(Protocol):
    id: int
    bot: bool
    display_name: str


class _Message(Protocol):
    author: _Author


class _Guild(Protocol):
    def get_member(self, user_id: int) -> _Author | None: ...


def chat_speakers(messages: Iterable[_Message], guild: _Guild) -> dict[int, str]:
    """Return current guild members who authored the recent channel messages."""
    speakers = {}
    for message in messages:
        author = message.author
        if author.bot or author.id in speakers:
            continue
        member = guild.get_member(author.id)
        if member is None and getattr(author, "guild", None) == guild:
            member = author
        if member and not member.bot:
            speakers[member.id] = member.display_name
    return speakers


def other_recent_speaker_names(
    speakers: Mapping[int, str], requester_id: int
) -> list[str]:
    return [name for user_id, name in speakers.items() if user_id != requester_id]


def render_user_mentions(text: str, speakers: Mapping[int, str]) -> str:
    """Show known speakers by name and hide unverified numeric mention IDs."""
    return USER_MENTION_PATTERN.sub(
        lambda match: speakers.get(int(match.group(1)), "someone"), text
    )
