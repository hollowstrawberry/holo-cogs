from datetime import datetime
from agent import constants
from agent.config_base import ConfigField, CogConfig, CogConfigBase


class AgentCogGuildConfig(CogConfigBase):
    # General
    channel_mode:            ConfigField[str]            = ConfigField("whitelist")
    channels:                ConfigField[list[int]]      = ConfigField([])
    generation_channel_mode: ConfigField[str]            = ConfigField("blacklist")
    generation_channels:     ConfigField[list[int]]      = ConfigField([])
    auto_channel_mode:       ConfigField[str]            = ConfigField("whitelist")
    auto_channels:           ConfigField[list[int]]      = ConfigField([])
    memory:                  ConfigField[dict[str, str]] = ConfigField({})
    prompt_keys:             ConfigField[dict[str, str]] = ConfigField({})
    enabled_functions:       ConfigField[list[str]]      = ConfigField(["update_memory", "agent_search", "scrape"])
    # LLM
    model_recaller:          ConfigField[str] = ConfigField("gpt-5.4-nano")
    model_responder:         ConfigField[str] = ConfigField("gpt-5.4-mini")
    model_memorizer:         ConfigField[str] = ConfigField("gpt-5.4-mini")
    model_captioner:         ConfigField[str] = ConfigField("gpt-5.4-nano")
    model_autoreacter:       ConfigField[str] = ConfigField("gpt-5.4-nano")
    effort_recaller:         ConfigField[str] = ConfigField("minimal")
    effort_responder:        ConfigField[str] = ConfigField("low")
    effort_memorizer:        ConfigField[str] = ConfigField("low")
    prompt_recaller:         ConfigField[str] = ConfigField(constants.DEFAULT_PROMPT_RECALLER)
    prompt_responder:        ConfigField[str] = ConfigField(constants.DEFAULT_PROMPT_RESPONDER)
    prompt_autoresponder:    ConfigField[str] = ConfigField(constants.DEFAULT_PROMPT_AUTORESPONDER)
    prompt_memorizer:        ConfigField[str] = ConfigField(constants.DEFAULT_PROMPT_MEMORIZER)
    prompt_captioner:        ConfigField[str] = ConfigField(constants.DEFAULT_PROMPT_CAPTIONER)
    prompt_autoreacter:      ConfigField[str] = ConfigField(constants.DEFAULT_PROMPT_AUTOREACTER)
    # Limits 
    response_tokens:         ConfigField[int] = ConfigField(1000)
    backread_tokens:         ConfigField[int] = ConfigField(2000)
    backread_messages:       ConfigField[int] = ConfigField(10)
    backread_short:          ConfigField[int] = ConfigField(5)
    max_tool_depth:          ConfigField[int] = ConfigField(3)
    max_images:              ConfigField[int] = ConfigField(1)
    max_image_resolution:    ConfigField[int] = ConfigField(1020)
    max_caption_resolution:  ConfigField[int] = ConfigField(380)
    max_quote:               ConfigField[int] = ConfigField(200)
    max_tool:                ConfigField[int] = ConfigField(3000)
    max_text_file:           ConfigField[int] = ConfigField(3000)
    # Memorizer
    allow_memorizer:         ConfigField[bool] = ConfigField(False)
    memorizer_user_only:     ConfigField[bool] = ConfigField(True)
    memorizer_alerts:        ConfigField[bool] = ConfigField(True)
    # Autoresponder
    autoresponder_chance:           ConfigField[float] = ConfigField(0.0)
    autoreacter_chance:             ConfigField[float] = ConfigField(0.0)
    autoreacter_chance_images:      ConfigField[float] = ConfigField(0.0)
    autoresponder_cooldown_minutes: ConfigField[int]   = ConfigField(60)
    autoreacter_cooldown_minutes:   ConfigField[int]   = ConfigField(5)


class AgentCogChannelConfig(CogConfigBase):
    start: ConfigField[datetime]         = ConfigField(constants.DISCORD_EPOCH_DATETIME)
    last_response: ConfigField[datetime] = ConfigField(constants.DISCORD_EPOCH_DATETIME)
    last_reaction: ConfigField[datetime] = ConfigField(constants.DISCORD_EPOCH_DATETIME)


class AgentCogConfig(CogConfig[AgentCogGuildConfig, AgentCogChannelConfig]):
    _guild_config = AgentCogGuildConfig
    _channel_config = AgentCogChannelConfig
    # Global
    extended_logging: ConfigField[bool]        = ConfigField(True)
    tool_settings: ConfigField[dict[str, str]] = ConfigField({})
    response_timeout: ConfigField[int]         = ConfigField(120)
    slow_timer: ConfigField[int]               = ConfigField(30)
    slow_emoji: ConfigField[str]               = ConfigField("🤔")
    noresponse_emoji: ConfigField[str]         = ConfigField("🤐")
    blocked_emoji: ConfigField[str]            = ConfigField("❌")
    status: ConfigField[str]                   = ConfigField("")
