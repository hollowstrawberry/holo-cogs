"""Bounded, read-only discovery of the currently registered prefix commands."""

import re
from copy import copy

from redbot.core import commands

from agent.schema import Function, Parameters, ToolCall
from agent.tools.base import ToolBase
from agent.utility_cogs import command_allowed


class CommandsTool(ToolBase):
    display_name = "commands"
    settings = {"command_search_results": "5", "command_help_chars": "1200"}
    schema = ToolCall(
        Function(
            name="commands",
            description="Search bot commands; exact names return usage.",
            parameters=Parameters(properties={"query": {"type": "string"}}, required=["query"]),
        )
    )

    def limit(self, name: str, default: int, maximum: int) -> int:
        try:
            return max(1, min(int(self.get_setting(name)), maximum))
        except (ValueError, TypeError):
            return default

    async def run(self, arguments: dict[str, object]) -> str:
        query = arguments.get("query")
        if set(arguments) != {"query"} or not isinstance(query, str) or not 0 < len(query.strip()) <= 200:
            return "Provide a nonempty query of up to 200 characters. This tool cannot run commands."
        query = " ".join(query.split())
        prefixes = await self.ctx.bot.get_valid_prefixes(self.ctx.guild)
        for prefix in sorted(prefixes, key=len, reverse=True):
            if prefix and query.startswith(prefix):
                query = query[len(prefix) :].strip()
                break
        if not query:
            return "Provide a command name or search term."
        prefix = next((p for p in prefixes if p and not p.startswith("<@")), prefixes[0] if prefixes else "")
        context = copy(self.ctx)
        context.prefix = prefix
        exact = context.bot.get_command(query)
        limit = self.limit("command_search_results", 5, 20)
        budget = self.limit("command_help_chars", 1200, 4000)
        if exact and await command_allowed(context, exact, visible=True):
            lines = [f"{prefix}{exact.qualified_name} {exact.signature}".rstrip()]
            help_text = exact.format_help_for_context(context) or exact.short_doc
            # Keep room for children; never render a full Discord help page.
            lines.append(help_text[: max(0, budget // 2)].strip())
            if isinstance(exact, commands.Group):
                children = []
                for child in sorted(exact.commands, key=lambda c: c.qualified_name):
                    if isinstance(child, commands.Command) and await command_allowed(context, child, visible=True):
                        children.append(f"{prefix}{child.qualified_name}: {child.short_doc[:120]}")
                        if len(children) >= limit:
                            break
                lines.extend(children)
            return "\n".join(line for line in lines if line)[:budget]

        words = query.casefold().split()
        candidates: list[tuple[int, str, commands.Command]] = []
        for command in context.bot.walk_commands():
            if not isinstance(command, commands.Command):
                continue
            names = " ".join([command.qualified_name, *command.aliases]).casefold()
            description = re.sub(r"\s+", " ", command.help or "").casefold()
            if all(word in f"{names} {description}" for word in words):
                score = sum(word in names for word in words)
                candidates.append((-score, command.qualified_name, command))
        matches = []
        for _, _, match in sorted(candidates, key=lambda item: (item[0], item[1])):
            if await command_allowed(context, match, visible=True):
                matches.append(f"{prefix}{match.qualified_name}: {match.short_doc[:120]}")
                if len(matches) >= limit:
                    break
        return "\n".join(matches)[:budget] if matches else "No visible commands found."
