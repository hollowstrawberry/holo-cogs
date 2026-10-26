import re
import discord
from typing import Protocol, runtime_checkable
from datetime import date, datetime, timezone
from redbot.core import Config

from agent.utils import get_cog
from agent.types import Function, Parameters, ToolCall
from agent.tools.base import ToolBase

MINIMUM_YEAR = 1900


@runtime_checkable
class Birthday(Protocol): # Vex-Cogs
    config: Config

    async def check_if_setup(self, guild: discord.Guild) -> bool: ...


class BirthdayTool(ToolBase):
    display_name = "birthday"
    direct_only = True
    required_cog = Birthday.__name__
    schema = ToolCall(
        Function(
            name="set_birthday",
            description="Set the requester's birthday in this server.",
            parameters=Parameters(
                properties={
                    "username": {  # checking for LLM intent makes me feel safer
                        "type": "string",
                    },
                    "date": {
                        "type": "string",
                        "description": "MM-DD or YYYY-MM-DD",
                    },
                },
                required=["username", "date"]
            ),
        )
    )

    async def run(self, arguments: dict[str, object]) -> str:
        assert self.ctx.guild
        username = str(arguments.get("username", "")).strip()
        value = str(arguments.get("date", "")).strip()
        if not username:
            return f"<error>You must provide the username of the user you are responding to.</error>"
        if username != self.ctx.author.name:
            return f"<error>You are responding to {self.ctx.author.name} but trying to set the birthday for {username}. The user must ask you directly.</error"
        try:
            if not re.fullmatch(r"(?:\d{4}-)?\d{2}-\d{2}", value):
                raise ValueError
            # Birthday reconstructs an omitted year as 1 when reading its Config.
            parsed = date.fromisoformat(value if len(value) == 10 else f"0001-{value}")
        except ValueError:
            return "<error>Invalid date. You must provide a date in the format MM-DD or YYYY-MM-DD.</error>"
        if not (cog := get_cog(self.cog.bot, Birthday)):
            return "<error>Birthday cog from Vex-Cogs is unavailable or is using a different version. Please notify the bot owner.</error>"
        if not await cog.check_if_setup(self.ctx.guild):
            return "<error>Birthday is not set up in this server. An admin must run bdset interactive.</error>"
        year = parsed.year if len(value) == 10 else None
        if year is not None and year < MINIMUM_YEAR:
            return f"<error>Birthday cannot be earlier than the year {MINIMUM_YEAR}.</error>"
        elif year is not None and parsed > datetime.now(timezone.utc).date():
            return f"<error>Birthday cannot be in the future.</error>" 

        await cog.config.member_from_ids(self.ctx.guild.id, self.ctx.author.id).birthday.set({
            "year": year,
            "month": parsed.month,
            "day": parsed.day,
        })
        return f"Birthday for {username} set to {value}"
