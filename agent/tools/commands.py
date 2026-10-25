import re
from copy import copy
from redbot.core import commands

from agent.utils import add_xml_group, command_allowed
from agent.schema import Function, Parameters, ToolCall
from agent.tools.base import ToolBase


class CommandsTool(ToolBase):
    display_name = "commands"
    settings = {
        "command_results": "10",
        "subcommand_results": "30",
        "command_help_chars": "1000",
        "command_brief_chars": "120"
    }
    schema = ToolCall(
        Function(
            name="commands",
            description="Search bot commands; exact names return usage.",
            parameters=Parameters(
                properties={
                    "query": {
                        "type": "string"
                    }
                },
                required=["query"]
            ),
        )
    )

    async def run(self, arguments: dict[str, object]) -> str | dict:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "<error>No query was given.</error>"
        query = " ".join(query.split())
        prefixes = await self.cog.bot.get_valid_prefixes(self.ctx.guild)
        for prefix in sorted(prefixes, key=len, reverse=True):
            if prefix and query.startswith(prefix):
                query = query[len(prefix) :].strip()
                break
        if not query:
            return "Provide a command name or search term."
        
        prefix = next((p for p in prefixes if p and not p.startswith("<@")), prefixes[0] if prefixes else "")
        context = copy(self.ctx)
        context.prefix = prefix
        exact = self.cog.bot.get_command(query)
        limit = self.get_setting_int("command_results", 1, 50) or 10
        child_limit = self.get_setting_int("subcommand_results", 1, 100) or 30
        help_budget = self.get_setting_int("command_help_chars", 100, 4000) or 1000
        brief_budget = self.get_setting_int("command_brief_chars", 50, 4000) or 120
        cmds: list[dict] = []

        if exact and await command_allowed(context, exact, visible=True):
            cmd = {"command": f"{prefix}{exact.qualified_name} {exact.signature}".rstrip()}
            help_text = exact.format_help_for_context(context) or exact.short_doc
            cmd["help"] = help_text[:help_budget].strip()
            if isinstance(exact, commands.Group):
                children = []
                for child in sorted(exact.walk_commands(), key=lambda c: c.qualified_name):
                    if isinstance(child, commands.Command) and await command_allowed(context, child, visible=True):
                        children.append(f"{prefix}{child.qualified_name}: {child.short_doc[:brief_budget].strip()}")
                        if len(children) >= child_limit:
                            break
                add_xml_group(cmd, children, "subcommands")
            cmds.append(cmd)

        if not exact:
            words = query.casefold().split()
            candidates: list[tuple[int, str, commands.Command]] = []
            for command in self.cog.bot.walk_commands():
                if not isinstance(command, commands.Command):
                    continue
                names = " ".join([command.qualified_name, *command.aliases]).casefold()
                description = re.sub(r"\s+", " ", command.help or "").casefold()
                if all(word in f"{names} {description}" for word in words):
                    score = sum(word in names for word in words)
                    candidates.append((-score, command.qualified_name, command))
            for _, _, match in sorted(candidates, key=lambda item: (item[0], item[1])):
                if await command_allowed(context, match, visible=True):
                    cmds.append({"#text": f"{prefix}{match.qualified_name}: {match.short_doc[:brief_budget].strip()}"})
                    if len(cmds) >= limit:
                        break

        result = {}
        add_xml_group(result, cmds, "commands")
        return {"result": result or "No visible commands found."}
