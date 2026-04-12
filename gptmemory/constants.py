import re
from typing import OrderedDict
from datetime import datetime, timezone
from discord.utils import DISCORD_EPOCH

MAX_MESSAGE_LENGTH = 1950
MAX_EMBED_DESCRIPTION = 4096
MAX_EMBED_FIELD = 1024
MAX_EMBED_NAME = 256
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
VIEW_TIMEOUT = 15 * 60
EMPTY = "ᅠ"

RESPONSE_CLEANUP_PATTERNS = OrderedDict({
    "Author system texts":          re.compile(r"^(\[[^[:\]]*:[^[:\]]*\]\s?)+", re.MULTILINE),
    "Single-line system texts":     re.compile(r"\[\[.+(\]\]|$)"),
    "Multiline system texts":       re.compile(r"\[\[\[[\s\S]+?(\]\]\]|$)"),
    "Automated actions":            re.compile(r"^\s*-?\s*#\s*(Request|Revise|Reroll|Result|Upscale|Change|Variation).+", re.MULTILINE | re.IGNORECASE),
    "Image objects":                re.compile(r"{[^}]*?(image|file|action)[^}]*?}(?!\s*```)", re.IGNORECASE),
    "Embeds":                       re.compile(r"\[\s*Embed[^\]]+\]", re.MULTILINE | re.IGNORECASE),
    "Loading message":              re.compile(r"`\s*[⏳⌛][^`]+`\s*"),
    "Leftover symbol":              re.compile(r"""\n[}'"\s\-]+$""")
})

GENERATE_IMAGE_PATTERNS = {
    "System action":     re.compile(r"\[\[.+?Generated.+?prompt:\]\s*(.+?)\s*\]\]", re.IGNORECASE),
    "Gemini action":     re.compile(r"""{\s*(?:["']action["'][\s\S]+?)?["']prompt["']:\s*["']([^"']+)["'][\s\S]*$""", re.IGNORECASE)
}

INCOMPLETE_EMOTE_PATTERN = re.compile(r"<?(a?:\w{2,}:\d{17,19})>?")
FARENHEIT_PATTERN = re.compile(r"(-?\d+)\s?°[fF]")
LORA_PATTERN = re.compile(r"(<lora:([^:]+):(\d+\.?\d*)>)")

URL_PATTERN = re.compile(r"(https?://\S+)")
GITHUB_FILE_URL_PATTERN = re.compile(r"(https?://)?github.com/(?P<user>[^/]+)/(?P<repo>[^/]+)/blob/(?P<branch>[^/]+)/(?P<path>.+)")
ARCENCIEL_MODEL_URL_PATTERN = re.compile(r"(https?://)?arcenciel.io/models/(?P<id>\d+)")
DISCORD_MESSAGE_LINK_PATTERN = re.compile(r"(https?://)?discord.com/channels/(?P<guild_id>\d+)/(?P<channel_id>\d+)/(?P<message_id>\d+)")

DISCORD_EPOCH_DATETIME = datetime.fromtimestamp(DISCORD_EPOCH / 1000, tz=timezone.utc)

VISION_MODELS = [
    "gpt-5.4",
    "gpt-5.4-pro",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "o3",
    "o4-mini",
    "o1",
    "gpt-4o",
    "gpt-4o-mini",
]

MODELS_THAT_USE_MINIMAL = [
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-5.4",
    "gpt-5.4-pro",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
]

EFFORT_VALUES = [
    "minimal",
    "low",
    "medium",
    "high",
]

IMAGEGEN_RESOLUTIONS = [
    (1024, 1024),
    (832, 1216),
    (1216, 832),
    (1536, 640),
]
