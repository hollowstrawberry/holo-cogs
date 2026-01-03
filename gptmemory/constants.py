import re
from datetime import datetime, timezone
from discord.utils import DISCORD_EPOCH

MAX_MESSAGE_LENGTH = 1950
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")

# ((^|\n)(\[[^[:\]]*:[^[:\]]*\]\s?)+)    Author system texts such as [Username: Crabot]
# (\[\[\[[\s\S]+\]\]\])                  Multiline system texts inside [[[ triple brackets ]]]
# (\[\[.+\]\])                           Single-line system texts inside [[ double brackets ]]
# (-# [Rr][Ee].+)                        Bot actions that are automated and the AI likes to repeat
# ({\s*"[^"]+":[^}]+}(?!\s*```))         json output that isn't followed by a code block
RESPONSE_CLEANUP_PATTERN = re.compile(r'(((^|\n)(\[[^[:\]]*:[^[:\]]*\]\s?)+)|(\[\[\[[\s\S]+\]\]\])|(\[\[.+\]\])|(-\s*#\s*[Rr][Ee].+)|({\s*"[^"]+":[^}]+}(?!\s*```)))')

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
