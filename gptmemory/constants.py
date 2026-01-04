import re
from typing import OrderedDict
from datetime import datetime, timezone
from discord.utils import DISCORD_EPOCH

MAX_MESSAGE_LENGTH = 1950
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")

RESPONSE_CLEANUP_PATTERNS = OrderedDict({
    "Author system texts":          re.compile(r"^(\[[^[:\]]*:[^[:\]]*\]\s?)+", re.MULTILINE),
    "Single-line system texts":     re.compile(r"\[\[.+\]\]"),
    "Multiline system texts":       re.compile(r"\[\[\[[\s\S]+\]\]\]"),
    "Automated actions":            re.compile(r"^-?\s*#\s*(Request|Revise|Reroll|Upscale|Change).+", re.MULTILINE | re.IGNORECASE),
    "Image objects":                re.compile(r"{[^}]*?image[^}]*?}(?!\s*```)", re.IGNORECASE),
})

GENERATE_IMAGE_PATTERN = re.compile(r"\[\[.+?Generated.+?prompt:\]\s*(.+?)\s*\]\]", re.IGNORECASE)
INCOMPLETE_EMOTE_PATTERN = re.compile(r"<?(a?:\w{2,}:\d{17,19})>?")
FARENHEIT_PATTERN = re.compile(r"(-?\d+)\s?°[fF]")
CODEBLOCK_PATTERN = re.compile(r"^```(\w*)\s*$")

URL_PATTERN = re.compile(r"(https?://\S+)")
GITHUB_FILE_URL_PATTERN = re.compile(r"(https?://)?github.com/(?P<user>[^/]+)/(?P<repo>[^/]+)/blob/(?P<branch>[^/]+)/(?P<path>.+)")
ARCENCIEL_MODEL_URL_PATTERN = re.compile(r"(https?://)?arcenciel.io/models/(?P<id>\d+)")
DISCORD_MESSAGE_LINK_PATTERN = re.compile(r"(https?://)?discord.com/channels/(?P<guild_id>\d+)/(?P<channel_id>\d+)/(?P<message_id>\d+)")

DISCORD_EPOCH_DATETIME = datetime.fromtimestamp(DISCORD_EPOCH / 1000, tz=timezone.utc)

VISION_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-5.1",
    "gpt-5.2",
]

MODELS_THAT_USE_MINIMAL = [
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
]

EFFORT_VALUES = [
    "minimal",
    "low",
    "medium",
    "high",
]
