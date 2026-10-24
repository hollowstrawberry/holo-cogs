"""Adapter for PCXCogs' RemindMe scheduler."""

from datetime import datetime, timezone

from agent.schema import Function, Parameters, ToolCall
from agent.tools.base import ToolBase
from agent.utility_cogs import ReminderBackend, utility_cog


class ReminderTool(ToolBase):
    display_name = "reminder"
    direct_only = True
    required_cog = "RemindMe"
    schema = ToolCall(
        Function(
            name="set_reminder",
            description="DM the requester after a delay in seconds.",
            parameters=Parameters(
                properties={"seconds": {"type": "integer", "minimum": 60}, "text": {"type": "string"}},
                required=["seconds", "text"],
            ),
        )
    )

    async def run(self, arguments: dict[str, object]) -> str:
        seconds, text = arguments.get("seconds"), arguments.get("text")
        if (
            set(arguments) != {"seconds", "text"}
            or type(seconds) is not int
            or seconds < 60
            or not isinstance(text, str)
        ):
            return "Provide seconds (integer, at least 60) and text."
        created = int(datetime.now(timezone.utc).timestamp())
        expires = created + seconds
        try:
            datetime.fromtimestamp(expires, timezone.utc)
        except (OverflowError, ValueError, OSError):
            return "Reminder time is too large."
        cog = await utility_cog(self.ctx, "RemindMe", "remindme")
        if not isinstance(cog, ReminderBackend) or type(cog.MAX_REMINDER_LENGTH) is not int:
            return "RemindMe unavailable or command not allowed."
        if len(text) > cog.MAX_REMINDER_LENGTH:
            return f"Reminder text exceeds {cog.MAX_REMINDER_LENGTH} characters."
        reminder: dict[str, object] = {
            "text": text,
            "created": created,
            "expires": expires,
            "jump_link": self.ctx.message.jump_url,
        }
        if not await cog.insert_reminder(self.ctx.author.id, reminder):
            return "Reminder not saved: your reminder limit has been reached."
        return f"Reminder saved for <t:{expires}:F>; delivery by DM."
