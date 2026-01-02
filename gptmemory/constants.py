import re
from datetime import datetime, timezone
from discord.utils import DISCORD_EPOCH

MAX_MESSAGE_LENGTH = 1950
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")

# (^(\[[^[:\]]*:[^[:\]]*\]\s?)+)         Author system texts such as [Username: Crabot]
# (\[\[\[[\s\S]+\]\]\])                  Multiline system texts inside [[[ triple brackets ]]]
# (\[\[.+\]\])                           Single-line system texts inside [[ double brackets ]]
# (-# (Requested|Revised).+)             Bot actions that are automated and the AI likes to repeat
# ({\s*"(ai_?generated|url)":[^}]+}))    Gemini boilerplate text for images
RESPONSE_CLEANUP_PATTERN = re.compile(r'((^(\[[^[:\]]*:[^[:\]]*\]\s?)+)|(\[\[\[[\s\S]+\]\]\])|(\[\[.+\]\])|(-# (Requested|Revised).+)|({\s*"(ai_generated|url)":[^}]+}))', re.MULTILINE)

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
