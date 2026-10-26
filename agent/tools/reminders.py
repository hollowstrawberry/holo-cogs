from typing import Protocol, runtime_checkable
from datetime import datetime, timezone

from agent.utils import get_cog
from agent.types import Function, Parameters, ToolCall
from agent.constants import DATETIME_FORMATTING
from agent.tools.base import ToolBase

MINIMUM_SECONDS = 60


@runtime_checkable
class RemindMe(Protocol): # PCXCogs
    MAX_REMINDER_LENGTH: int

    async def insert_reminder(self, user_id: int, reminder: dict) -> bool: ...


class ReminderTool(ToolBase):
    display_name = "reminder"
    direct_only = True
    required_cog = RemindMe.__name__
    schema = ToolCall(
        Function(
            name="set_reminder",
            description="DM the requester after a delay in seconds.",
            parameters=Parameters(
                properties={
                    "username": {  # checking for LLM intent makes me feel safer
                        "type": "string",
                    },
                    "seconds": {
                        "type": "integer",
                        "minimum": MINIMUM_SECONDS
                    },
                    "text": {
                        "type": "string"
                    }},
                required=["username", "seconds", "text"],
            ),
        )
    )

    async def run(self, arguments: dict[str, object]) -> str | dict:
        username = str(arguments.get("username", "")).strip()
        text = str(arguments.get("text", "")).strip()
        seconds = arguments.get("seconds")
        if not username:
            return f"<error>You must provide the username of the user you are responding to.</error>"
        if username != self.ctx.author.name:
            return f"<error>You are responding to {self.ctx.author.name} but trying to set the birthday for {username}. The user must ask you directly.</error"
        if not text:
            return f"<error>Invalid argument, missing reminder text.</error>"
        if not isinstance(seconds, int) or seconds < MINIMUM_SECONDS:
            return f"<error>Invalid argument, seconds must be an integer higher than {MINIMUM_SECONDS - 1}.</error>"
        
        created = int(datetime.now(timezone.utc).timestamp())
        expires = created + seconds
        try:
            dt = datetime.fromtimestamp(expires, timezone.utc)
        except (OverflowError, ValueError, OSError):
            return "<error>Reminder time is too large.</error>"
        
        if not (cog := get_cog(self.cog.bot, RemindMe)):
            return "<error>RemindMe cog from PCXCogs is unavailable or is using a different version. Please notify the bot owner.</error>"
        if len(text) > cog.MAX_REMINDER_LENGTH:
            return f"<error>Reminder text exceeds {cog.MAX_REMINDER_LENGTH} characters.</error>"
        
        reminder = {
            "text": text,
            "created": created,
            "expires": expires,
            "jump_link": self.ctx.message.jump_url,
        }
        if not await cog.insert_reminder(self.ctx.author.id, reminder):
            return "<error>The user currently has too many active reminders.</error>"
        
        return {
            "message": f"Reminder for {username} will be sent at {dt.strftime(DATETIME_FORMATTING)}",
            "discord_formatted_string": f"<t:{expires}:R>",
            "tip": "User must have DMs enabled to receive this reminder",
        }
