import re
from typing import OrderedDict
from datetime import datetime, timezone
from discord.utils import DISCORD_EPOCH

MAX_MESSAGE_LENGTH = 1950
MAX_EMBED_DESCRIPTION = 4096
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
VIEW_TIMEOUT = 15 * 60
EMPTY = "ᅠ"
DATETIME_FORMATTING = "%Y-%m-%d %H:%M:%S %Z%z"
TOKEN_ENCODING = "o200k_base"
PROMPT_TYPES = ("responder", "recaller", "memorizer", "autoresponder")

RESPONSE_CONTENT_PATTERN = re.compile(r"<content>([\s\S]*?)</content>")
RESPONSE_CLEANUP_PATTERNS = OrderedDict({
    "Automated actions":            re.compile(r"^\s*-?\s*#\s*(Request|Revise|Reroll|Result|Upscale|Change|Variation).+", re.MULTILINE | re.IGNORECASE),
    "Image objects":                re.compile(r"{[^}]*?(image|file|action)[^}]*?}(?!\s*```)", re.IGNORECASE),
    "Leftover symbol":              re.compile(r"""\n[}'"\s\-]+$""")
})

GENERATE_IMAGE_PATTERNS = {
    "XML":               re.compile(r"<generated_image[\s\S]+?<prompt>([\s\S]*?)</prompt>"),
    "Gemini action":     re.compile(r"""{\s*(?:["']action["'][\s\S]+?)?["']prompt["']:\s*["']([^"']+)["'][\s\S]*$""", re.IGNORECASE)
}

INCOMPLETE_EMOTE_PATTERN = re.compile(r"<?(a?:\w{2,}:\d{17,19})>?")
FARENHEIT_PATTERN = re.compile(r"(-?\d+)\s?°[fF]")
LORA_PATTERN = re.compile(r"(<lora:([^:]+):(\d+\.?\d*)>)")
BACKTICK_PATTERN = re.compile(r"```+")
NEWLINE_SEPARATOR_PATTERN = re.compile(r",? *\n[\n\s]*")
UNCLOSED_XML_TAG_PATTERN = re.compile(r"<[^>]*$")
XML_TAG_PATTERN = re.compile(r"<(/)?([a-zA-Z0-9_]+)[^>]*?(/)?>")

URL_PATTERN = re.compile(r"(https?://\S+)")
GITHUB_FILE_URL_PATTERN = re.compile(r"(https?://)?github.com/(?P<user>[^/]+)/(?P<repo>[^/]+)/blob/(?P<branch>[^/]+)/(?P<path>.+)")
ARCENCIEL_MODEL_URL_PATTERN = re.compile(r"(https?://)?arcenciel.io/models/(?P<id>\d+)")
DISCORD_MESSAGE_LINK_PATTERN = re.compile(r"(?:https?://)?discord.com/channels/(?P<guild_id>\d+)/(?P<channel_id>\d+)/(?P<message_id>\d+)")

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
