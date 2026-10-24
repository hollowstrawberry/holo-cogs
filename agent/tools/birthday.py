"""Adapter for Vex-Cogs' Birthday cog; no external command execution."""

import re
from datetime import date, datetime, timezone
from importlib import import_module

from redbot.core import Config

from agent.schema import Function, Parameters, ToolCall
from agent.tools.base import ToolBase
from agent.utility_cogs import BirthdayBackend, utility_cog


class BirthdayTool(ToolBase):
    display_name = "birthday"
    direct_only = True
    required_cog = "Birthday"
    schema = ToolCall(
        Function(
            name="set_birthday",
            description="Set the requester's birthday in this server. Date: MM-DD or YYYY-MM-DD.",
            parameters=Parameters(properties={"date": {"type": "string"}}, required=["date"]),
        )
    )

    async def run(self, arguments: dict[str, object]) -> str:
        value = arguments.get("date")
        if (
            set(arguments) != {"date"}
            or not isinstance(value, str)
            or not re.fullmatch(r"(?:[0-9]{4}-)?[0-9]{2}-[0-9]{2}", value)
        ):
            return "Use date MM-DD or YYYY-MM-DD."
        try:
            # Birthday reconstructs an omitted year as 1 when reading its Config.
            parsed = date.fromisoformat(value if len(value) == 10 else f"0001-{value}")
        except ValueError:
            return "Invalid birthday date."

        cog = await utility_cog(self.ctx, "Birthday", "birthday set")
        if not isinstance(cog, BirthdayBackend) or not isinstance(cog.config, Config):
            return "Birthday unavailable or command not allowed."
        assert self.ctx.guild is not None
        if not await cog.check_if_setup(self.ctx.guild):
            return "Birthday is not set up in this server. An admin must run bdset interactive."
        try:
            minimum_year = getattr(import_module("birthday.consts"), "MIN_BDAY_YEAR", None)
        except ImportError:
            return "Unsupported Birthday cog version."
        if type(minimum_year) is not int:
            return "Unsupported Birthday cog version."
        year = parsed.year if len(value) == 10 else None
        if year is not None and (year < minimum_year or parsed > datetime.now(timezone.utc).date()):
            return f"Birth year must be at least {minimum_year}; date cannot be in the future."
        await cog.config.member_from_ids(self.ctx.guild.id, self.ctx.author.id).birthday.set(
            {
                "year": year,
                "month": parsed.month,
                "day": parsed.day,
            }
        )
        return f"Birthday saved: {value}."
