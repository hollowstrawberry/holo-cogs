"""Exercise the real responder loop with SDK responses and an inert tool."""

import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import tiktoken
from agent.agent import AgentCog
from agent.cache_policy import fingerprint
from agent.config_commands import AgentCogConfigCommands
from agent.schema import CompletionResult, Function, Parameters, ToolCall
from agent.tools.base import ToolBase
from openai.types.chat import ChatCompletion
from redbot.core import commands


class FixtureTool(ToolBase):
    display_name = "cache_fixture"
    schema = ToolCall(Function("cache_fixture", "Read a fixture", Parameters({})))

    async def run(self, arguments):
        return "fixture result"


class ResponderCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_loop_preserves_prefix_metadata_and_snapshots_config(self):
        for mode in ("implicit", "fixed_prefix"):
            with self.subTest(mode=mode):
                await self.check_loop(mode)
        await self.check_loop("fixed_prefix", model="native-model")

    async def check_loop(self, mode, model="fixture/model"):
        guild = MagicMock(spec=discord.Guild)
        guild.name = "Fixture guild"
        guild.me = MagicMock(spec=discord.Member)
        guild.me.name, guild.me.nick = "Fixture bot", None
        channel = MagicMock(spec=discord.TextChannel)
        channel.name = "fixture"
        ctx = MagicMock(spec=commands.Context)
        ctx.guild, ctx.channel, ctx.me = guild, channel, guild.me
        ctx.message = MagicMock(spec=discord.Message)
        ctx.message.id = 12345
        values = {
            "model_responder": model,
            "prompt_responder": "System {memories}",
            "prompt_autoresponder": "Auto",
            "prompt_keys": {"end": "End", "prefill": ""},
            "effort_responder": "low",
            "response_tokens": 200,
            "max_tool_depth": 3,
            "max_tool": 2000,
            "cache_mode": mode,
            "enabled_functions": ["cache_fixture"],
        }
        config = SimpleNamespace(**{key: SimpleNamespace(value=value) for key, value in values.items()})
        configs = MagicMock()
        configs.__getitem__.return_value = config
        configs.extended_logging.value = True
        requests = []

        async def complete(**kwargs):
            requests.append(deepcopy(kwargs))
            step = len(requests)
            message = {
                "role": "assistant",
                "content": "Done" if step == 3 else None,
                "reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque-signature"}],
            }
            if step < 3:
                message["tool_calls"] = [
                    {"id": f"call_{step}", "type": "function", "function": {"name": "cache_fixture", "arguments": "{}"}}
                ]
            # Simulate an owner editing configuration while the first request is pending.
            config.effort_responder.value = "high"
            config.response_tokens.value = 10
            config.max_tool_depth.value = 1
            config.max_tool.value = 1
            config.cache_mode.value = "implicit"
            config.prompt_keys.value["end"] = "Changed"
            return ChatCompletion.model_validate(
                {
                    "id": f"generation_{step}",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "fixture/model",
                    "provider": "fixture",
                    "choices": [
                        {"index": 0, "finish_reason": "stop" if step == 3 else "tool_calls", "message": message}
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "total_tokens": 110,
                        "prompt_tokens_details": {"cached_tokens": 80, "cache_write_tokens": 20},
                        "cost": 0.001,
                    },
                }
            )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))
        agent = SimpleNamespace(
            config=configs,
            encoding=tiktoken.get_encoding("o200k_base"),
            available_tools={FixtureTool},
            get_client=lambda model: client,
            bot=MagicMock(),
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "https://example.invalid/fixture.png"}},
                    {"type": "text", "text": "Private fixture text"},
                ],
            }
        ]
        original = deepcopy(messages)
        result = CompletionResult()
        with (
            patch("agent.agent.utils.chunk_and_send", new_callable=AsyncMock) as send,
            self.assertLogs("agent", level="INFO") as logs,
        ):
            response = await AgentCog.execute_responder(agent, ctx, messages, [], "Memory", result)
        self.assertEqual(response["content"], "Done")
        send.assert_awaited_once()
        self.assertEqual(messages, original)
        self.assertEqual(len(requests), 3)
        self.assertEqual([r["tool_choice"] for r in requests], ["auto", "auto", "none"])
        initial_count = len(requests[0]["messages"])
        prefix = fingerprint(requests[0]["messages"])
        for request in requests:
            self.assertEqual(fingerprint(request["messages"][:initial_count]), prefix)
            self.assertEqual(request["max_completion_tokens"], 200)
            self.assertEqual(request["reasoning_effort"], "low")
            self.assertEqual(request["extra_body"], {"session_id": "12345"} if "/" in model else None)
            self.assertEqual(request["tools"], requests[0]["tools"])
        self.assertEqual(
            str(requests[0]["messages"]).count("cache_control"), int(mode == "fixed_prefix" and "/" in model)
        )
        assistant = requests[1]["messages"][initial_count]
        self.assertEqual(assistant.model_extra["reasoning_details"][0]["data"], "opaque-signature")
        self.assertEqual(requests[1]["messages"][-1]["content"][0]["text"], "fixture result")
        self.assertNotIn("cache_control", str(requests[1]["messages"][-1]))
        self.assertEqual(result.input_tokens, 300)
        self.assertEqual(result.tokens.cached, 240)
        self.assertAlmostEqual(result.cost, 0.003)
        metrics = [
            json.loads(record.getMessage().split("model_call=", 1)[1])
            for record in logs.records
            if record.getMessage().startswith("model_call=")
        ]
        self.assertEqual(len(metrics), 3)
        for metric in metrics:
            self.assertTrue(metric["prefix_unchanged"])
            self.assertEqual(metric["usage"]["prompt_tokens_details"]["cache_write_tokens"], 20)
            self.assertNotIn("Private fixture", json.dumps(metric))

    async def test_cache_mode_command_uses_literal_converter(self):
        command = AgentCogConfigCommands.agentconfig_cache_mode
        parameter = command.clean_params["mode"]
        from typing import get_args

        self.assertEqual(set(get_args(get_args(parameter.annotation)[0])), {"implicit", "fixed_prefix"})


if __name__ == "__main__":
    unittest.main()
