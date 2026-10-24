"""Utility tool contracts using real Red command and permission objects."""

import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import tiktoken
from agent.agent import AgentCog
from agent.schema import CompletionResult, MemoryChangeResult
from agent.tools.base import get_all_tools
from agent.tools.birthday import BirthdayTool
from agent.tools.commands import CommandsTool
from agent.tools.reminders import ReminderTool
from agent.tools.update_memory import UpdateMemoryTool
from agent.utility_cogs import command_allowed
from discord.ext.commands.view import StringView
from openai import NOT_GIVEN
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageFunctionToolCall
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands.requires import PermState


class Birthday(commands.Cog):
    def __init__(self):
        super().__init__()
        self.config = MagicMock(spec=Config)
        self.saved = {}
        self.config.member_from_ids.return_value.birthday.set = AsyncMock(side_effect=self.saved.update)
        self.check_if_setup = AsyncMock(return_value=True)

    @commands.group(aliases=["bday"])
    async def birthday(self, ctx):
        """Manage birthdays."""
        raise AssertionError("The tool must not execute a command")

    @birthday.command(aliases=["add"])
    async def set(self, ctx, *, birthday: str):
        """Set your birthday. Example: [p]birthday set 31 Jul 1999."""
        raise AssertionError("The tool must not execute a command")

    @birthday.command(hidden=True)
    async def secret(self, ctx):
        """Hidden birthday command."""
        raise AssertionError("The tool must not execute a command")


class RemindMe(commands.Cog):
    MAX_REMINDER_LENGTH = 800

    def __init__(self):
        super().__init__()
        self.insert_reminder = AsyncMock(return_value=True)

    @commands.command()
    async def remindme(self, ctx, *, text: str):
        """Create a reminder."""
        raise AssertionError("The tool must not execute a command")


class UtilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = MagicMock(spec=Red)
        self.bot.user = MagicMock(spec=discord.ClientUser)
        self.bot.user.id = 99
        self.bot.user.name = "Fubuki"
        self.bot.get_valid_prefixes = AsyncMock(return_value=["<@99> ", "?"])
        self.bot.allowed_by_whitelist_blacklist = AsyncMock(return_value=True)
        self.bot.ignored_channel_or_guild = AsyncMock(return_value=True)
        self.bot.cog_disabled_in_guild = AsyncMock(return_value=False)
        self.bot.can_run = AsyncMock(return_value=True)
        self.bot.is_owner = AsyncMock(return_value=False)
        self.bot.verify_permissions_hooks = AsyncMock(return_value=None)

        member = MagicMock(spec=discord.Member)
        member.id, member.bot, member.voice, member.roles = 123, False, None, []
        guild = MagicMock(spec=discord.Guild)
        guild.id, guild.owner_id, guild.me = 456, 789, self.bot.user
        channel = MagicMock(spec=discord.TextChannel)
        channel.id, channel.category = 321, None
        channel.permissions_for.return_value = discord.Permissions.all()
        message = MagicMock(spec=discord.Message)
        message.id, message.author, message.guild, message.channel = 987, member, guild, channel
        message.mentions = [self.bot.user]
        message.jump_url = "https://discord.com/channels/456/321/987"
        self.ctx = commands.Context(bot=self.bot, message=message, view=StringView(""))

        self.birthday, self.reminder = Birthday(), RemindMe()
        self.cogs = {"Birthday": self.birthday, "RemindMe": self.reminder}
        self.root = commands.Group(type(self).empty, name="root")
        for cog in self.cogs.values():
            cog.requires.ready_event.set()
            for command in cog.get_commands():
                self.root.add_command(command)
                command.parent = None
            for command in cog.walk_commands():
                command.cog = cog
                command.requires.reset()
                command.requires.ready_event.set()
        self.bot.get_cog.side_effect = self.cogs.get
        self.bot.get_command.side_effect = self.root.get_command
        self.bot.walk_commands.side_effect = self.root.walk_commands
        self.agent = SimpleNamespace(bot=self.bot, config=SimpleNamespace(tool_settings=SimpleNamespace(value={})))
        self.constant = patch("agent.tools.birthday.import_module", return_value=SimpleNamespace(MIN_BDAY_YEAR=1900))
        self.constant.start()
        self.addCleanup(self.constant.stop)

    async def empty(self, ctx):
        raise AssertionError("No command execution")

    async def test_discovery_alias_prefix_and_help(self):
        result = await CommandsTool(self.ctx, self.agent).run({"query": "?bday add"})
        self.assertIn("?birthday set <birthday>", result)
        self.assertIn("?birthday set 31 Jul 1999", result)
        self.assertIsNone(self.ctx.command)

    async def test_group_hides_hidden_children(self):
        result = await CommandsTool(self.ctx, self.agent).run({"query": "birthday"})
        self.assertIn("?birthday set", result)
        self.assertNotIn("secret", result)

    async def test_search_bounds_and_configuration(self):
        self.agent.config.tool_settings.value = {"command_search_results": "1", "command_help_chars": "70"}
        result = await CommandsTool(self.ctx, self.agent).run({"query": "birthdays"})
        self.assertLessEqual(len(result), 70)
        self.assertEqual(len(result.splitlines()), 1)
        self.assertIn("birthday", result)
        self.agent.config.tool_settings.value = {"command_search_results": "invalid", "command_help_chars": "invalid"}
        self.assertIn("birthday", await CommandsTool(self.ctx, self.agent).run({"query": "birthday"}))

    async def test_search_never_accepts_run_or_empty_query(self):
        tool = CommandsTool(self.ctx, self.agent)
        for arguments in ({"query": "birthday set", "run": True}, {"query": ""}, {"query": []}, {"query": "x" * 201}):
            with self.subTest(arguments=arguments):
                self.assertIn("cannot run", await tool.run(arguments))
        self.assertIn("search term", await tool.run({"query": "?"}))

    async def test_actual_red_permissions_hidden_parent_and_guild_disable(self):
        tool = CommandsTool(self.ctx, self.agent)
        self.assertEqual(await tool.run({"query": "birthday secret"}), "No visible commands found.")
        self.birthday.birthday.deny_to(self.ctx.author.id, self.ctx.guild.id)
        self.assertFalse(await command_allowed(self.ctx, self.birthday.set))
        self.assertEqual(self.ctx.permission_state, PermState.NORMAL)
        self.assertIsNone(self.ctx.command)
        self.birthday.birthday.clear_rule_for(self.ctx.author.id, self.ctx.guild.id)
        self.birthday.set.disable_in(self.ctx.guild)
        self.assertFalse(await command_allowed(self.ctx, self.birthday.set))
        self.birthday.set.enable_in(self.ctx.guild)
        self.birthday.birthday.disable_in(self.ctx.guild)
        self.assertFalse(await command_allowed(self.ctx, self.birthday.set))

    async def test_global_checks_and_disabled_cog_block_writes(self):
        tool = BirthdayTool(self.ctx, self.agent)
        for method, denied in (
            ("allowed_by_whitelist_blacklist", False),
            ("ignored_channel_or_guild", False),
            ("cog_disabled_in_guild", True),
            ("can_run", False),
        ):
            with self.subTest(method=method):
                mock = self.bot.__dict__[method]
                previous = mock.return_value
                mock.return_value = denied
                self.assertIn("not allowed", await tool.run({"date": "1999-07-31"}))
                mock.return_value = previous
        self.assertEqual(self.birthday.saved, {})

    async def test_birthday_is_scoped_to_requesting_member_and_guild(self):
        result = await BirthdayTool(self.ctx, self.agent).run({"date": "1999-07-31"})
        self.assertEqual(result, "Birthday saved: 1999-07-31.")
        self.birthday.config.member_from_ids.assert_called_once_with(456, 123)
        self.assertEqual(self.birthday.saved, {"year": 1999, "month": 7, "day": 31})

    async def test_birthday_optional_year_and_leap_day(self):
        result = await BirthdayTool(self.ctx, self.agent).run({"date": "07-31"})
        self.assertIn("saved", result)
        self.assertEqual(self.birthday.saved, {"year": None, "month": 7, "day": 31})
        self.assertIn("Invalid", await BirthdayTool(self.ctx, self.agent).run({"date": "02-29"}))

    async def test_birthday_validation_and_setup(self):
        tool = BirthdayTool(self.ctx, self.agent)
        for value in ("1999/307/31", "2001-02-29", "13-01", "1899-01-01", "9999-01-01", "0001-01-01", True):
            with self.subTest(value=value):
                self.assertNotIn("saved", await tool.run({"date": value}))
        self.birthday.check_if_setup.return_value = False
        self.assertIn("not set up", await tool.run({"date": "07-31"}))
        self.assertEqual(self.birthday.saved, {})

    async def test_birthday_uses_external_minimum_year(self):
        with patch("agent.tools.birthday.import_module", return_value=SimpleNamespace(MIN_BDAY_YEAR=1950)):
            self.assertIn("1950", await BirthdayTool(self.ctx, self.agent).run({"date": "1949-01-01"}))
        self.assertEqual(self.birthday.saved, {})

    async def test_writes_reject_missing_cog_wrong_command_owner_and_auto_context(self):
        tool = BirthdayTool(self.ctx, self.agent)
        self.ctx.message.mentions = []
        self.assertIn("not allowed", await tool.run({"date": "07-31"}))
        self.ctx.message.mentions = [self.bot.user]
        self.birthday.set.cog = self.reminder
        self.assertIn("not allowed", await tool.run({"date": "07-31"}))
        self.cogs.pop("Birthday")
        self.assertIn("unavailable", await tool.run({"date": "07-31"}))
        self.assertEqual(self.birthday.saved, {})

    async def test_member_identity_cannot_be_overridden(self):
        result = await BirthdayTool(self.ctx, self.agent).run({"date": "07-31", "user_id": 999})
        self.assertNotIn("saved", result)
        result = await ReminderTool(self.ctx, self.agent).run({"seconds": 60, "text": "Hi", "user_id": 999})
        self.assertNotIn("saved", result)
        self.reminder.insert_reminder.assert_not_awaited()

    async def test_reminder_uses_scheduler_and_original_message(self):
        start = int(datetime.now(timezone.utc).timestamp())
        result = await ReminderTool(self.ctx, self.agent).run({"seconds": 7200, "text": "Laundry"})
        self.assertIn("delivery by DM", result)
        user_id, reminder = self.reminder.insert_reminder.call_args.args
        self.assertEqual(user_id, 123)
        self.assertEqual(reminder["jump_link"], self.ctx.message.jump_url)
        self.assertEqual(reminder["text"], "Laundry")
        self.assertGreaterEqual(reminder["created"], start)
        self.assertEqual(reminder["expires"] - reminder["created"], 7200)

    async def test_reminder_limits_and_invalid_input(self):
        tool = ReminderTool(self.ctx, self.agent)
        for seconds in (True, 0, 59, "60", 60.5, 10**100):
            with self.subTest(seconds=seconds):
                self.assertNotIn("saved", await tool.run({"seconds": seconds, "text": "x"}))
        self.reminder.MAX_REMINDER_LENGTH = 5
        self.assertIn("exceeds 5", await tool.run({"seconds": 60, "text": "123456"}))
        self.reminder.insert_reminder.assert_not_awaited()
        self.reminder.insert_reminder.return_value = False
        self.assertIn("limit", await tool.run({"seconds": 60, "text": "x"}))

    async def test_reminder_permissions_are_checked(self):
        self.reminder.remindme.deny_to(self.ctx.author.id, self.ctx.guild.id)
        result = await ReminderTool(self.ctx, self.agent).run({"seconds": 60, "text": "x"})
        self.assertIn("not allowed", result)
        self.reminder.insert_reminder.assert_not_awaited()

    async def test_repeated_writes_are_deduplicated_within_response_only(self):
        cache = {}
        first = await ReminderTool(self.ctx, self.agent).run_cached({"seconds": 60, "text": "x"}, cache)
        second = await ReminderTool(self.ctx, self.agent).run_cached({"text": "x", "seconds": 60}, cache)
        self.assertEqual(first, second)
        self.reminder.insert_reminder.assert_awaited_once()
        await ReminderTool(self.ctx, self.agent).run_cached({"seconds": 60, "text": "x"}, {})
        self.assertEqual(self.reminder.insert_reminder.await_count, 2)

    async def test_existing_memory_tool_keeps_non_json_arguments(self):
        tool = UpdateMemoryTool(self.ctx, self.agent)
        tool.run = AsyncMock(return_value="ok")
        arguments = {"changes": [MemoryChangeResult("name", "before", "after")]}
        self.assertEqual(await tool.run_cached(arguments, {}), "ok")
        tool.run.assert_awaited_once_with(arguments)

    async def test_opt_in_tools_auto_filter_and_schema_budget(self):
        for cls in (CommandsTool, BirthdayTool, ReminderTool):
            self.assertIn(cls, get_all_tools())
            self.assertTrue(cls.available_for(self.ctx, auto=False))
        self.assertTrue(CommandsTool.available_for(self.ctx, auto=True))
        self.assertFalse(BirthdayTool.available_for(self.ctx, auto=True))
        self.assertFalse(ReminderTool.available_for(self.ctx, auto=True))
        self.cogs.clear()
        self.assertFalse(BirthdayTool.available_for(self.ctx, auto=False))
        self.assertFalse(ReminderTool.available_for(self.ctx, auto=False))
        schemas = [cls.asdict() for cls in (CommandsTool, BirthdayTool, ReminderTool)]
        self.assertLessEqual(len(tiktoken.get_encoding("o200k_base").encode(json.dumps(schemas))), 220)

    async def responder(self, *, auto, calls=(), enabled=("commands", "birthday", "reminder")):
        self.ctx.guild.me = MagicMock(spec=discord.Member)
        self.ctx.guild.me.name, self.ctx.guild.me.nick = "Fubuki", None
        guild_config = SimpleNamespace(
            **{
                name: SimpleNamespace(value=value)
                for name, value in {
                    "model_responder": "test-model",
                    "prompt_responder": "Responder",
                    "prompt_autoresponder": "Autoresponder",
                    "prompt_keys": {},
                    "enabled_functions": enabled,
                    "max_tool_depth": 3,
                    "effort_responder": "none",
                    "response_tokens": 200,
                    "max_tool": 2000,
                }.items()
            }
        )
        config = MagicMock()
        config.__getitem__.return_value = guild_config
        config.extended_logging.value = False
        config.tool_settings.value = {}
        self.agent.config = config
        self.agent.available_tools = {ReminderTool, BirthdayTool, CommandsTool}
        self.agent.encoding = tiktoken.get_encoding("o200k_base")
        response = SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    message=ChatCompletionMessage(
                        role="assistant",
                        content=None if calls else "Done",
                        tool_calls=list(calls) or None,
                    )
                )
            ],
        )
        final = SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    message=ChatCompletionMessage(
                        role="assistant",
                        content="Done",
                    )
                )
            ],
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(side_effect=[response, final]),
                )
            )
        )
        self.agent.get_client = MagicMock(return_value=client)
        with patch("agent.agent.utils.chunk_and_send", new_callable=AsyncMock):
            await AgentCog.execute_responder(self.agent, self.ctx, [], set(), "", CompletionResult(), auto)
        return client.chat.completions.create.call_args_list

    async def test_responder_omits_write_schemas_for_auto_responses(self):
        calls = await self.responder(auto=True)
        self.assertEqual([tool["function"]["name"] for tool in calls[0].kwargs["tools"]], ["commands"])
        self.reminder.insert_reminder.assert_not_awaited()

    async def test_responder_omits_empty_tool_parameters(self):
        self.cogs.clear()
        calls = await self.responder(auto=False, enabled=("birthday", "reminder"))
        self.assertIs(calls[0].kwargs["tools"], NOT_GIVEN)
        self.assertIs(calls[0].kwargs["tool_choice"], NOT_GIVEN)

    async def test_responder_deduplicates_actual_model_tool_calls(self):
        calls = [
            ChatCompletionMessageFunctionToolCall(
                id=f"call_{i}",
                type="function",
                function={"name": "set_reminder", "arguments": json.dumps({"seconds": 60, "text": "Laundry"})},
            )
            for i in range(2)
        ]
        requests = await self.responder(auto=False, calls=calls)
        self.reminder.insert_reminder.assert_awaited_once()
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            [tool["function"]["name"] for tool in requests[0].kwargs["tools"]],
            ["set_birthday", "commands", "set_reminder"],
        )


if __name__ == "__main__":
    unittest.main()
