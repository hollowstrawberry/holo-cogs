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
PERMANENT_PROMPT_TYPES = ("responder", "autoresponder", "autoreacter", "recaller", "captioner", "memorizer")
MAX_IMAGES_PER_MESSAGE = 4

RESPONSE_CLEANUP_PATTERNS = [
    #("Opening XML",       re.compile(r"^\s*<chat_message(?: [^>]+)?>\s*<content>\s*", re.DOTALL | re.IGNORECASE), ""),
    ("Quote",             re.compile(r"\s*<quote>.*?</quote>\s*", re.DOTALL | re.IGNORECASE), r""),
    ("Multiple messages", re.compile(r"^\s*<chat_message(?: [^>]+)?>(.*?)</chat_message>\s*<chat_message.+</chat_message>\s*$", re.DOTALL | re.IGNORECASE), r"\1"),  # the second one is greedy
    ("Trailing message",  re.compile(r"^(.{9,}?[^>])\s*<chat_message.+$", re.DOTALL | re.IGNORECASE), r"\1"),
    ("Message content",   re.compile(r"^.*?<content>(.*?)</content>.*$", re.DOTALL | re.IGNORECASE), r"\1"),
    #("Closing content",   re.compile(r"^(.+?)\s*</content>\s*</chat_message>.*$", re.DOTALL | re.IGNORECASE), r"\1"),
    ("XML objects",       re.compile(r"\s*<(linked_message|message_link|embeds?|attachments?|images?|stickers?|buttons?|reactions?|poll)(?: [^>]+)?>.*?</\1>\s*", re.DOTALL | re.IGNORECASE), ""),
    ("Automated actions", re.compile(r"^\s*-?\s*#\s*(Request|Revise|Reroll|Result|Upscale|Change|Variation).+", re.MULTILINE | re.IGNORECASE), ""),
    ("JSON objects",      re.compile(r"{[^}]*?(image|file|action)[^}]*?}(?!\s*`{3,})", re.IGNORECASE), ""),
    ("Markdown image",    re.compile(r"!\[.*?\]\(.+?\.(?:jpe?g|png|gif|webp)(?:[&?].*?)?\)", re.IGNORECASE), ""),
    ("Closing XML",       re.compile(r"(?:\s*</(?:chat_message|content)>)+\s*$"), ""),
    ("Leftover symbol",   re.compile(r"""\n[}'"\s\-]+$"""), ""),
    ("Leftover symbol 2", re.compile(r"""^[}'"\s\-]+\n"""), ""),
    #("Server emote",      re.compile(r"`?(?:&lt;|<)?(a?:\w+:\d{17,19})(?:&gt;|>)?`?"), r"<\1>"),
    ("Em dash",           re.compile(r"(?<=\w)\s*—\s*(?=\w)"), ", "),
    ("Em dash 2",         re.compile(r"(?<=[.!?)])\s*—\s*"), " "),
    ("Tone modifier",     re.compile(r"\[(pause?|happy|angry|sad|soft|excited|laugh|whisper|yell|scream|cry|sobb?|moan|sigh|sing|clear(s|ing)?.throat)(ing)?\]", re.IGNORECASE), ""),
]
GENERATE_IMAGE_PATTERNS = [
    ("XML object strict", re.compile(r"<generated_image(?: [^>]+)?>(?:(?!</generated_image>).)*<prompt>(.*?)</prompt>(?:(?!</generated_image>).)*</generated_image>", re.DOTALL | re.IGNORECASE)),
    ("XML object",        re.compile(r"<(\w+)(?: [^>]+)?>(?:(?!</\1>).)*<prompt>(.*?)</prompt>(?:(?!</\1>).)*</\1>", re.DOTALL | re.IGNORECASE)),
    #"JSON object":       re.compile(r"""{\s*(?:["']action["'].+?)?["']prompt["']:\s*["']([^"']+)["'].*$""", re.DOTALL | re.IGNORECASE),
]
EMOTE_PATTERN = re.compile(r"<(a?):(\w+):(\d{17,19})>")
INCOMPLETE_EMOTE_PATTERN = re.compile(r"`?\\?(?:&lt;|<)?(a?):(\w{3,}):(\d*)(?:&gt;|>)?`?")
FAKE_EMOTE_PATTERN = re.compile(r"(?:^|\s+):\w+:(?:\s+|$)")

ALPHANUMERIC_PATTERN = re.compile(r"^(\w+)$")
FARENHEIT_PATTERN = re.compile(r"(-?\d+)\s?°[fF]")
LORA_PATTERN = re.compile(r"(<lora:([^:]+):(\d+\.?\d*)>)")
BACKTICK_PATTERN = re.compile(r"```+")
NEWLINE_SEPARATOR_PATTERN = re.compile(r",? *\n[\n\s]*")
PIPE_SEPARATOR_PATTERN = re.compile(r"\s*\|\|\s*")
UNCLOSED_XML_TAG_PATTERN = re.compile(r"<[^>]*$")
XML_TAG_PATTERN = re.compile(r"<(/)?(\w+)[^>]*?(/)?>")

URL_PATTERN = re.compile(r"(https?://\S+)")
GITHUB_FILE_URL_PATTERN = re.compile(r"(https?://)?github.com/(?P<user>[^/]+)/(?P<repo>[^/]+)/blob/(?P<branch>[^/]+)/(?P<path>.+)")
ARCENCIEL_MODEL_URL_PATTERN = re.compile(r"(https?://)?arcenciel.io/models/(?P<id>\d+)")
DISCORD_MESSAGE_LINK_PATTERN = re.compile(r"(?:https?://)?discord.com/channels/(?P<guild_id>\d+)/(?P<channel_id>\d+)/(?P<message_id>\d+)")

DISCORD_EPOCH_DATETIME = datetime.fromtimestamp(DISCORD_EPOCH / 1000, tz=timezone.utc)

MEDIA_HEADERS =  {
    "User-Agent": "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "Accept": "image/*;q=0.9",
}

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
    "none",
    "minimal",
    "low",
    "medium",
    "high",
]

SD_IMAGEGEN_RESOLUTIONS = [
    (1024, 1024),
    (832, 1216),
    (1216, 832),
    (1536, 640),
]

FAKE_TOOL_CALL = [
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "retrieve_data_cmV0cmlldmVfZGF0YQ",
            "type": "function",
            "function": {
                "name": "retrieve_data",
                "arguments": "{}"
            }
        }]
    },
    {
        "role": "tool",
        "content": "<error>Tool calls have been disabled. Let the user know whether you were able to complete the task or not.</error>",
        "tool_call_id": "retrieve_data_cmV0cmlldmVfZGF0YQ",
    },
]

DEFAULT_PROMPT_RECALLER = """\
You are a conversation parser. You will be given a list of topics as well as a conversation between various users, \
and your objective is to provide the names of the topics relevant to the conversation. \
Here are all the available topics, separated by commas:
{0}\
"""

DEFAULT_PROMPT_RESPONDER = """\
Your identity is {botname}, a digital assistant in the {servername} Discord server. \
Provide a concise response to the latest message. You're always helpful with questions, \
but be honest when you don't know something or are unsure about it.

The current datetime is {currentdatetime}. The current channel is #{channelname}. \
A user can ask you to remember or forget something about themselves, but nothing else.

{memories}\
"""

DEFAULT_PROMPT_AUTORESPONDER = """\
Your identity is {botname}, a digital assistant in the {servername} Discord server. \
Respond in a way that participates in the current conversation, and don't be annoying.

The current datetime is {currentdatetime}. The current channel is #{channelname}.

{memories}\
"""

DEFAULT_PROMPT_MEMORIZER = """\
You are the memory manager of a conversational agent with username '{botname}' and alias '{botnickname}'. \
You will analyze a chat history involving one or more users. \
In the unique case that a user explicitly asks the agent to remember or forget something about themselves, \
you may edit that user's memory in one of several ways. The user should never be able to edit memories that are not about themselves. \
The memory for a user should only change if that specific user explicitly communicates their desire to do so. \
The desire to remember or forget something must be directed at the agent for it to be valid. \
It's expected that in most cases you shall return an empty list.

Memory entries are defined by a username. There are different ways to edit a memory:
- If a memory for that username doesn't exist, you may create it.
- To remember something new, you should append to the memory.
- To forget something or to change a part of the memory, you may modify it. \
In this case, you are tasked to change the memory entry as little as possible except for the necessary changes.

Don't be gullible with information that may potentially be untrustworthy. 

The available entries are as follows, separated by commas:
{0}

Below are the contents of some of the entries:

{1}\
"""

DEFAULT_PROMPT_CAPTIONER = """\
You are an image captioning subagent. You must provide a descriptive and accurate caption for a given image. \
Include text transcriptions. Sexually explicit content is allowed and must be described correctly.\
"""

DEFAULT_PROMPT_AUTOREACTER = """\
Your identity is {botname}, a digital assistant in the {servername} Discord server. \
You must provide an emoji that will be sent as a reaction to the latest message.\
Pick the emoji that is most fitting.\
"""
