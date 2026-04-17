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

RESPONSE_CLEANUP_PATTERNS = [
    ("multiple messages", re.compile(r"^\s*<chat_message(?: [^>]+)?>(.*?)</chat_message>\s*<chat_message.+</chat_message>\s*$", re.DOTALL | re.IGNORECASE), r"\1"),
    ("trailing message",  re.compile(r"^(.{10,}?)\s*<chat_message.+</chat_message>\s*$", re.DOTALL | re.IGNORECASE), r"\1"),
    ("quote",             re.compile(r"\s*<quote>.*?</quote>\s*", re.DOTALL | re.IGNORECASE), r""),
    ("message content",   re.compile(r"^.*?<content>(.*?)</content>.*$", re.DOTALL | re.IGNORECASE), r"\1"),
    ("closing content",   re.compile(r"^(.+?)\s*</content>\s*</chat_message>.*$", re.DOTALL | re.IGNORECASE), r"\1"),
    ("XML objects",       re.compile(r"\s*<(linked_message|message_link|embeds?|attachments?|images?|stickers?|buttons?|reactions?|poll)(?: [^>]+)?>.*?</\1>\s*", re.DOTALL | re.IGNORECASE), ""),
    ("Automated actions", re.compile(r"^\s*-?\s*#\s*(Request|Revise|Reroll|Result|Upscale|Change|Variation).+", re.MULTILINE | re.IGNORECASE), ""),
    ("JSON objects",      re.compile(r"{[^}]*?(image|file|action)[^}]*?}(?!\s*`{3,})", re.IGNORECASE), ""),
    ("Closing XML",       re.compile(r"(\s*</\w+>)+\s*$"), ""),
    ("Leftover symbol",   re.compile(r"""\n[}'"\s\-]+$"""), ""),
    ("Server emote",      re.compile(r"`?(?:&lt;|<)?(a?:\w+:\d{17,19})(?:&gt;|>)?`?"), r"<\1>"),
    ("Em dash",           re.compile(r"(?<=\w)—(?=\w)"), ", "),
]
GENERATE_IMAGE_PATTERNS = {
    "XML object":        re.compile(r"<(\w+)(?: [^>]+)?>.*?<prompt>(.*?)<\/prompt>.*?<\/\1>", re.DOTALL | re.IGNORECASE),
    "JSON object":       re.compile(r"""{\s*(?:["']action["'].+?)?["']prompt["']:\s*["']([^"']+)["'].*$""", re.DOTALL | re.IGNORECASE),
}
INCOMPLETE_EMOTE_PATTERN = re.compile(r"`?(?:&lt;|<)?a?:(\w{3,}):\d{0,16}(?!\d)(?:&gt;|>)?`?")

FARENHEIT_PATTERN = re.compile(r"(-?\d+)\s?°[fF]")
LORA_PATTERN = re.compile(r"(<lora:([^:]+):(\d+\.?\d*)>)")
BACKTICK_PATTERN = re.compile(r"```+")
NEWLINE_SEPARATOR_PATTERN = re.compile(r",? *\n[\n\s]*")
UNCLOSED_XML_TAG_PATTERN = re.compile(r"<[^>]*$")
XML_TAG_PATTERN = re.compile(r"<(/)?(\w+)[^>]*?(/)?>")

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
