import aiohttp
from datetime import datetime
from openai import AsyncOpenAI
from redbot.core import commands, Config
from redbot.core.bot import Red

import gptmemory.defaults as defaults
from gptmemory.schema import CompletionResult, GptImageContent
from gptmemory.config import ConfigField, CogConfig, GuildConfigBase, ChannelConfigBase
from gptmemory.constants import DISCORD_EPOCH_DATETIME


class GptMemoryGuildConfig(GuildConfigBase):
    # General
    channel_mode:            ConfigField[str]            = ConfigField("whitelist")
    channels:                ConfigField[list[int]]      = ConfigField([])
    generation_channel_mode: ConfigField[str]            = ConfigField("blacklist")
    generation_channels:     ConfigField[list[int]]      = ConfigField([])
    auto_channel_mode:       ConfigField[str]            = ConfigField("whitelist")
    auto_channels:           ConfigField[list[int]]      = ConfigField([])
    memory:                  ConfigField[dict[str, str]] = ConfigField({})
    prompt_keys:             ConfigField[dict[str, str]] = ConfigField({})
    enabled_functions:       ConfigField[list[str]]      = ConfigField(defaults.ENABLED_FUNCTIONS)
    # LLM
    model_recaller:          ConfigField[str] = ConfigField(defaults.MODEL_RECALLER)
    model_responder:         ConfigField[str] = ConfigField(defaults.MODEL_RESPONDER)
    model_memorizer:         ConfigField[str] = ConfigField(defaults.MODEL_MEMORIZER)
    model_captioner:         ConfigField[str] = ConfigField(defaults.MODEL_CAPTIONER)
    model_autoreacter:       ConfigField[str] = ConfigField(defaults.MODEL_AUTOREACTER)
    prompt_recaller:         ConfigField[str] = ConfigField(defaults.PROMPT_RECALLER)
    prompt_responder:        ConfigField[str] = ConfigField(defaults.PROMPT_RESPONDER)
    prompt_autoresponder:    ConfigField[str] = ConfigField(defaults.PROMPT_AUTORESPONDER)
    prompt_memorizer:        ConfigField[str] = ConfigField(defaults.PROMPT_MEMORIZER)
    prompt_captioner:        ConfigField[str] = ConfigField(defaults.PROMPT_CAPTIONER)
    prompt_autoreacter:      ConfigField[str] = ConfigField(defaults.PROMPT_AUTOREACTER)
    effort_recaller:         ConfigField[str] = ConfigField(defaults.EFFORT_RECALLER)
    effort_responder:        ConfigField[str] = ConfigField(defaults.EFFORT_RESPONDER)
    effort_memorizer:        ConfigField[str] = ConfigField(defaults.EFFORT_MEMORIZER)
    # Limits 
    response_tokens:         ConfigField[int] = ConfigField(defaults.RESPONSE_TOKENS)
    backread_tokens:         ConfigField[int] = ConfigField(defaults.BACKREAD_TOKENS)
    backread_messages:       ConfigField[int] = ConfigField(defaults.BACKREAD_MESSAGES)
    backread_short:          ConfigField[int] = ConfigField(defaults.BACKREAD_SHORT)
    max_images:              ConfigField[int] = ConfigField(defaults.IMAGES_PER_CONTEXT)
    max_quote:               ConfigField[int] = ConfigField(defaults.QUOTE_LENGTH)
    max_tool:                ConfigField[int] = ConfigField(defaults.TOOL_CALL_LENGTH)
    max_tool_depth:          ConfigField[int] = ConfigField(defaults.TOOL_DEPTH)
    max_text_file:           ConfigField[int] = ConfigField(defaults.TEXT_FILE_LENGTH)
    max_image_resolution:    ConfigField[int] = ConfigField(defaults.IMAGE_SIZE)
    max_caption_resolution:  ConfigField[int] = ConfigField(defaults.CAPTION_SIZE)
    # Memorizer
    allow_memorizer:         ConfigField[bool] = ConfigField(defaults.ALLOW_MEMORIZER)
    memorizer_user_only:     ConfigField[bool] = ConfigField(defaults.MEMORIZER_USER_ONLY)
    memorizer_alerts:        ConfigField[bool] = ConfigField(defaults.MEMORIZER_ALERTS)
    # Autoresponder
    autoresponder_chance:           ConfigField[float] = ConfigField(0.0)
    autoreacter_chance:             ConfigField[float] = ConfigField(0.0)
    autoreacter_chance_images:      ConfigField[float] = ConfigField(0.0)
    autoresponder_cooldown_minutes: ConfigField[int]   = ConfigField(60)
    autoreacter_cooldown_minutes:   ConfigField[int]   = ConfigField(5)


class GptMemoryChannelConfig(ChannelConfigBase):
    start: ConfigField[datetime]         = ConfigField(DISCORD_EPOCH_DATETIME)
    last_response: ConfigField[datetime] = ConfigField(DISCORD_EPOCH_DATETIME)
    last_reaction: ConfigField[datetime] = ConfigField(DISCORD_EPOCH_DATETIME)


class GptMemoryConfig(CogConfig[GptMemoryGuildConfig, GptMemoryChannelConfig]):
    guild_type = GptMemoryGuildConfig
    channel_type = GptMemoryChannelConfig
    # Global
    extended_logging: ConfigField[bool]        = ConfigField(True)
    tool_settings: ConfigField[dict[str, str]] = ConfigField({})
    response_timeout: ConfigField[int]         = ConfigField(120)
    slow_timer: ConfigField[int]               = ConfigField(30)
    slow_emoji: ConfigField[str]               = ConfigField("🤔")
    noresponse_emoji: ConfigField[str]         = ConfigField("🤐")
    blocked_emoji: ConfigField[str]            = ConfigField("❌")


class GptMemoryBase(commands.Cog):
    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.openai_client: AsyncOpenAI | None = None
        self.openrouter_client: AsyncOpenAI | None = None
        self.openwebui_client: AsyncOpenAI | None = None
        self.currently_responding: set[int] = set()
        self.currently_generating: set[int] = set()
        self.config = GptMemoryConfig(Config.get_conf(self, identifier=19475820))
        self.config.register_all()
        
    async def find_last_sd_generated_image_resolution(self, ctx: commands.Context) -> tuple[int | None, int | None]:
        raise NotImplementedError()
    
    async def execute_captioner(self, ctx: commands.Context, image: GptImageContent, result: CompletionResult) -> str:
        raise NotImplementedError()
    
    def is_busy(self, message_id):
        return message_id in self.currently_responding or message_id in self.currently_generating
