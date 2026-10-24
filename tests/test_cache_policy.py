import unittest
from copy import deepcopy

from agent.cache_policy import fingerprint, prepare_messages
from agent.config import AgentCogGuildConfig
from agent.utils import get_text_contents, make_text_content
from openai.types.chat import ChatCompletionMessage


class CachePolicyTests(unittest.TestCase):
    def test_default_cache_mode(self):
        self.assertEqual(AgentCogGuildConfig.cache_mode.value, "fixed_prefix")

    def test_mixed_content_keeps_all_text_and_normalizes_roles(self):
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,test"}}
        messages = [
            {"role": "assistant", "content": [image, make_text_content("first"), image, make_text_content("second")]},
            {"role": "user", "content": "plain"},
            {"role": "user", "content": [image]},
        ]
        original = deepcopy(messages)
        self.assertEqual(
            get_text_contents(messages),
            [{"role": "user", "content": "first\nsecond"}, {"role": "user", "content": "plain"}],
        )
        self.assertEqual(messages, original)

    def test_implicit_removes_old_markers_without_mutating_context(self):
        messages = [
            {"role": "system", "content": "system", "cache_control": {"type": "ephemeral"}},
            {"role": "user", "content": [{"type": "text", "text": "question", "cache_control": {"type": "ephemeral"}}]},
        ]
        original = deepcopy(messages)
        prepared = prepare_messages(messages, "implicit")
        self.assertNotIn("cache_control", str(prepared))
        self.assertEqual(messages, original)

    def test_fixed_boundary_precedes_trailing_system_and_prefill(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "history"},
            {"role": "user", "content": [make_text_content("question")]},
            {"role": "system", "content": "end"},
            {"role": "assistant", "content": "prefill"},
        ]
        prepared = prepare_messages(messages, "fixed_prefix")
        self.assertEqual(str(prepared).count("cache_control"), 1)
        self.assertIn("cache_control", prepared[2]["content"][0])
        before = fingerprint(prepared)
        prepared.append({"role": "tool", "content": [make_text_content("result")], "tool_call_id": "1"})
        self.assertEqual(before, fingerprint(prepared[:-1]))
        self.assertNotIn("cache_control", str(prepared[-1]))

    def test_fixed_fallback_empty_and_invalid_modes(self):
        self.assertEqual(prepare_messages([], "fixed_prefix"), [])
        self.assertIn("cache_control", str(prepare_messages([{"role": "system", "content": "s"}], "fixed_prefix")))
        with self.assertRaises(ValueError):
            prepare_messages([], "unsupported")

    def test_fingerprint_preserves_sdk_reasoning_metadata(self):
        message = ChatCompletionMessage.model_validate(
            {
                "role": "assistant",
                "content": None,
                "reasoning_details": [{"type": "reasoning.encrypted", "data": "signature"}],
            }
        )
        before = message.model_dump()
        self.assertEqual(fingerprint([message]), fingerprint([message.model_dump(mode="json", exclude_unset=True)]))
        self.assertEqual(message.model_dump(), before)
        self.assertNotEqual(fingerprint([message]), fingerprint([{"role": "assistant", "content": None}]))


if __name__ == "__main__":
    unittest.main()
