from typing import Dict
from redbot.core import commands, Config
from redbot.core.bot import Red

import gptmemory.defaults as defaults
from gptmemory.constants import DISCORD_EPOCH_DATETIME


class GptMemoryConfig(commands.Cog):
    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.memory: Dict[int, Dict[str, str]] = {}
        self.extended_logging = True
        self.config = Config.get_conf(self, identifier=19475820)
        
        self.config.register_global(**{
            "extended_logging": True
        })
        self.config.register_channel(**{
            "start": DISCORD_EPOCH_DATETIME.isoformat(),
        })
        self.config.register_guild(**{
            "channel_mode": "whitelist",
            "channels": [],
            "generation_channel_mode": "blacklist",
            "generation_channels": [],
            "memory": {},
            "model_recaller": defaults.MODEL_RECALLER,
            "model_responder": defaults.MODEL_RESPONDER,
            "model_memorizer": defaults.MODEL_MEMORIZER,
            "prompt_recaller": defaults.PROMPT_RECALLER,
            "prompt_responder": defaults.PROMPT_RESPONDER,
            "prompt_memorizer": defaults.PROMPT_MEMORIZER,
            "effort_recaller": defaults.EFFORT_RECALLER,
            "effort_responder": defaults.EFFORT_RESPONDER,
            "effort_memorizer": defaults.EFFORT_MEMORIZER,
            "response_tokens": defaults.RESPONSE_TOKENS,
            "backread_tokens": defaults.BACKREAD_TOKENS,
            "backread_messages": defaults.BACKREAD_MESSAGES,
            "backread_memorizer": defaults.BACKREAD_MEMORIZER,
            "allow_memorizer": defaults.ALLOW_MEMORIZER,
            "memorizer_user_only": defaults.MEMORIZER_USER_ONLY,
            "memorizer_alerts": defaults.MEMORIZER_ALERTS,
            "disabled_functions": list(defaults.DISABLED_FUNCTIONS),
            "emotes": "",
            "max_images": defaults.IMAGES_PER_CONTEXT,
            "max_quote": defaults.QUOTE_LENGTH,
            "max_tool": defaults.TOOL_CALL_LENGTH,
            "max_text_file": defaults.TEXT_FILE_LENGTH,
            "max_image_resolution": defaults.IMAGE_SIZE,
        })
