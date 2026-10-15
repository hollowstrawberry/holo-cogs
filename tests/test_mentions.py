import runpy
from pathlib import Path
from types import SimpleNamespace


mentions = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "agent" / "mentions.py")
)
chat_speakers = mentions["chat_speakers"]
other_recent_speaker_names = mentions["other_recent_speaker_names"]
render_user_mentions = mentions["render_user_mentions"]


class Guild:
    def __init__(self, members):
        self.members = {member.id: member for member in members}

    def get_member(self, user_id):
        return self.members.get(user_id)


def member(user_id, name, *, bot=False):
    return SimpleNamespace(id=user_id, display_name=name, bot=bot)


def message(author):
    return SimpleNamespace(author=author)


def test_recent_speakers_are_current_humans_in_channel_order():
    requester = member(11, "Requester")
    newest = member(22, "Recent Friend")
    older = member(33, "Older Friend")
    bot = member(44, "Another Bot", bot=True)
    departed = member(55, "Departed")
    guild = Guild([requester, newest, older, bot])

    speakers = chat_speakers(
        [
            message(requester),
            message(newest),
            message(bot),
            message(newest),
            message(departed),
            message(older),
        ],
        guild,
    )

    assert speakers == {11: "Requester", 22: "Recent Friend", 33: "Older Friend"}
    assert other_recent_speaker_names(speakers, requester.id) == [
        "Recent Friend",
        "Older Friend",
    ]


def test_uncached_member_from_this_guild_can_be_a_recent_speaker():
    guild = Guild([])
    author = member(22, "Uncached Friend")
    author.guild = guild

    assert chat_speakers([message(author)], guild) == {22: "Uncached Friend"}
    assert other_recent_speaker_names({22: "Uncached Friend"}, 22) == []


def test_numeric_mentions_resolve_only_for_verified_speakers():
    text = "*punches <@183023020613271552> beside <@!22> and <@33>*"

    assert render_user_mentions(text, {22: "Recent Friend", 33: "Older Friend"}) == (
        "*punches someone beside Recent Friend and Older Friend*"
    )
    assert render_user_mentions("<@&123> <@not-an-id>", {}) == "<@&123> <@not-an-id>"
