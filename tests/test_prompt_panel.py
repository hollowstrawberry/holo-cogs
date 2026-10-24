"""Focused prompt-panel checks with real discord.py components and mocked I/O."""

import asyncio
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
from agent.commands import AgentCogCommands
from agent.config_base import ConfigField
from agent.constants import PERMANENT_PROMPT_TYPES
from agent.views.prompt_edit_modal import PromptEditModal
from agent.views.prompt_show import PromptView
from agent.views.prompt_store import PromptRef, PromptStore
from agent.views.prompt_panel import PromptPanelView
from redbot.core import commands


def field(name, value):
    group = MagicMock()
    getattr(group, name).set = AsyncMock()
    return ConfigField(value).load(name, group, {name: value})


def store(keys=None):
    return PromptStore(
        {name: field("prompt_" + name, "module " + name) for name in PERMANENT_PROMPT_TYPES},
        field("prompt_keys", {"imagegen": "private prompt content"} if keys is None else keys),
    )


def event(owner=True, manage=False):
    message = NS(edit=AsyncMock(), delete=AsyncMock())
    return NS(
        user=NS(owner=owner),
        permissions=NS(manage_messages=manage),
        message=message,
        response=NS(send_message=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock(), defer=AsyncMock()),
        followup=NS(send=AsyncMock()),
        original_response=AsyncMock(return_value=message),
    )


async def is_owner(user):
    return user.owner


class PromptPanelTests(unittest.IsolatedAsyncioTestCase):
    def panel(self, data=None):
        view = PromptPanelView(data or store(), is_owner, "Fubuki Prompt Panel", discord.Color.blue())
        self.addCleanup(view.stop)
        return view

    def detail(self, data=None, ref=None, private=True):
        view = PromptView(
            data or store(), ref or PromptRef("key", "imagegen"), is_owner, discord.Color.blue(), private=private
        )
        self.addCleanup(view.stop)
        return view

    async def test_v2_panel_pages_include_all_keys_empty_and_long_names(self):
        keys = {f"key{i:02}": "value" for i in range(51)}
        keys.update({"empty": "", "x" * 150: "long key", "autoreacter": "custom autoreacter"})
        view = self.panel(store(keys))
        self.assertTrue(view.has_components_v2())
        self.assertLessEqual(view.total_children_count, 40)
        seen = set()
        for page in range(3):
            self.assertEqual(view.page, page)
            self.assertLessEqual(len(view.key_select.options), 25)
            for option in view.key_select.options:
                self.assertLessEqual(len(option.label), 100)
                self.assertLessEqual(len(option.value), 100)
                seen.add(view.key_select.refs[option.value].name)
            self.assertLessEqual(len(json.dumps(view.to_components())), 10000)
            await view.next(event())
        self.assertEqual(seen, set(keys))
        self.assertEqual(view.page, 2)
        await view.previous(event())
        self.assertEqual(view.page, 1)
        await view.store.keys.set({})
        await view.reload(event())
        self.assertEqual(view.page, 0)
        self.assertTrue(view.key_select.disabled)
        self.assertEqual(len(view.module_select.options), 6)

    async def test_select_opens_current_private_prompt_in_correct_namespace(self):
        data = store({"autoreacter": "custom secret", "empty": ""})
        view = self.panel(data)
        self.assertNotIn("custom secret", json.dumps(view.to_components()))
        for select, kind in ((view.module_select, "module"), (view.key_select, "key")):
            select._values = [next(k for k, ref in select.refs.items() if ref.name == "autoreacter")]
            interaction = event(owner=False)
            await select.callback(interaction)
            kwargs = interaction.response.send_message.call_args.kwargs
            detail = kwargs["view"]
            self.addCleanup(detail.stop)
            self.assertTrue(kwargs["ephemeral"])
            self.assertEqual(detail.ref, PromptRef(kind, "autoreacter"))
            self.assertEqual(detail.message, interaction.message)
        selected = view.key_select
        selected._values = [next(k for k, ref in selected.refs.items() if ref.name == "empty")]
        await data.keys.set({"autoreacter": "new value"})
        interaction = event()
        await selected.callback(interaction)
        self.assertIn("no longer exists", interaction.response.send_message.call_args.args[0])

    async def test_editor_reads_latest_value_and_saves_only_selected_target(self):
        data = store({"autoreacter": "old custom", "other": "preserved"})
        view = self.detail(data, PromptRef("key", "autoreacter"))
        await data.keys.set({**data.keys.value, "autoreacter": "fresh custom"})
        interaction = event()
        await view.edit_prompt(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        self.assertEqual(modal.prompt_input.default, "fresh custom")
        modal.prompt_input._value = "updated custom"
        await modal.on_submit(event())
        self.assertEqual(data.keys.value, {"autoreacter": "updated custom", "other": "preserved"})
        self.assertEqual(data.modules["autoreacter"].value, "module autoreacter")
        view = self.detail(data, PromptRef("module", "autoreacter"))
        interaction = event()
        await view.edit_prompt(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        modal.prompt_input._value = "updated module"
        await modal.on_submit(event())
        self.assertEqual(data.modules["autoreacter"].value, "updated module")
        self.assertEqual(data.keys.value["autoreacter"], "updated custom")

    async def test_stale_edit_and_duplicate_creation_do_not_overwrite(self):
        data = store({"imagegen": "original", "empty": ""})
        ref = PromptRef("key", "imagegen")
        modal = PromptEditModal(data, ref, is_owner, lambda: True, AsyncMock())
        self.addCleanup(modal.stop)
        await data.keys.set({**data.keys.value, "imagegen": "someone else's edit"})
        modal.prompt_input._value = "stale edit"
        interaction = event()
        await modal.on_submit(interaction)
        self.assertEqual(data.get(ref), "someone else's edit")
        self.assertIn("changed", interaction.followup.send.call_args.args[0])
        for name in ("imagegen", "empty"):
            modal = PromptEditModal(data, None, is_owner, lambda: True, AsyncMock())
            self.addCleanup(modal.stop)
            modal.name_input._value, modal.prompt_input._value = name, "replacement"
            interaction = event()
            await modal.on_submit(interaction)
            self.assertIn("already exists", interaction.followup.send.call_args.args[0])
        self.assertEqual(data.keys.value["empty"], "")

    async def test_create_custom_key_including_module_name_and_empty_value(self):
        data = store({})
        view = self.panel(data)
        view.message = event().message
        interaction = event()
        await view.create_prompt(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        modal.name_input._value, modal.prompt_input._value = "autoreacter", ""
        result = event()
        await modal.on_submit(result)
        self.assertEqual(data.keys.value, {"autoreacter": ""})
        self.assertIn("autoreacter** created", result.followup.send.call_args.args[0])
        self.assertNotIn("None", result.followup.send.call_args.args[0])
        self.assertEqual(view.key_select.options[0].description, "Empty")
        view.message.edit.assert_awaited_once()

    async def test_owner_permissions_rechecked_on_submit_and_close_is_moderated(self):
        data = store()
        view, detail = self.panel(data), self.detail(data)
        for action in (view.create_prompt, detail.edit_prompt):
            interaction = event(owner=False)
            await action(interaction)
            interaction.response.send_modal.assert_not_awaited()
            self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
        modal = PromptEditModal(data, PromptRef("key", "imagegen"), is_owner, lambda: True, AsyncMock())
        self.addCleanup(modal.stop)
        modal.prompt_input._value = "unauthorized edit"
        await modal.on_submit(event(owner=False))
        self.assertEqual(data.keys.value["imagegen"], "private prompt content")
        interaction = event(owner=False)
        await view.close(interaction)
        interaction.message.delete.assert_not_awaited()
        interaction = event(owner=False, manage=True)
        await view.close(interaction)
        interaction.message.delete.assert_awaited_once()
        self.assertTrue(view.is_finished())

    async def test_expired_panel_and_invalid_names_never_write(self):
        data = store({})
        view = self.panel(data)
        interaction = event()
        await view.create_prompt(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        modal.name_input._value = "test"
        view.stop()
        await modal.on_submit(event())
        self.assertEqual(data.keys.value, {})
        for name in ("   ` `", "x" * 101):
            modal = PromptEditModal(data, None, is_owner, lambda: True, AsyncMock())
            self.addCleanup(modal.stop)
            modal.name_input._value = name
            await modal.on_submit(event())
        self.assertEqual(data.keys.value, {})

    async def test_simultaneous_creations_preserve_other_keys_and_reject_duplicate(self):
        data = store({})

        async def persist(value):
            await asyncio.sleep(0)

        data.keys._group.__getattr__("prompt_keys").set.side_effect = persist
        results = await asyncio.gather(
            data.save(PromptRef("key", "a"), "first", None),
            data.save(PromptRef("key", "b"), "second", None),
            data.save(PromptRef("key", "a"), "duplicate", None),
            return_exceptions=True,
        )
        self.assertEqual(data.keys.value, {"a": "first", "b": "second"})
        self.assertIsInstance(results[2], ValueError)

    async def test_modal_limits_and_valid_4000_character_save(self):
        data = store({"imagegen": "x" * 4000})
        detail = self.detail(data)
        interaction = event()
        await detail.edit_prompt(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        payload = modal.to_dict()
        self.assertEqual(payload["components"][0]["component"]["max_length"], 4000)
        modal.prompt_input._value = "y" * 4000
        await modal.on_submit(event())
        self.assertEqual(data.keys.value["imagegen"], "y" * 4000)
        await detail.on_timeout()

    async def test_long_prompt_private_preview_and_lossless_download(self):
        prompt = "@everyone ``` <|endoftext|> 😀\n" * 200
        data = store({"imagegen": prompt})
        public = self.detail(data, private=False)
        self.assertNotIn("@everyone", json.dumps(public.to_components()))
        private = self.detail(data)
        self.assertLess(private.content_length(), 4000)
        interaction = event()
        await private.edit_prompt(interaction)
        interaction.response.send_modal.assert_not_awaited()
        self.assertIn("4,000", interaction.response.send_message.call_args.args[0])
        await data.keys.set({"imagegen": prompt + "new"})
        interaction = event(owner=False)
        await private.download(interaction)
        kwargs = interaction.response.send_message.call_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["file"].fp.read().decode(), prompt + "new")
        kwargs["file"].close()

    async def test_emptying_key_keeps_it_accessible_and_refreshes_details(self):
        data = store()
        detail = self.detail(data)
        detail.message = event().message
        interaction = event()
        await detail.edit_prompt(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.addCleanup(modal.stop)
        modal.prompt_input._value = ""
        await modal.on_submit(event())
        self.assertIn("Empty prompt", json.dumps(detail.to_components()))
        self.assertIn("imagegen", data.keys.value)
        self.assertEqual(self.panel(data).key_select.options[0].description, "Empty")
        detail.message.edit.assert_awaited_once()

    async def test_direct_command_panel_and_full_export(self):
        data = store({"imagegen": "custom content"})
        await data.modules["responder"].set("Start {imagegen} end")
        config = NS(prompt_keys=data.keys, **{"prompt_" + k: v for k, v in data.modules.items()})
        cog = object.__new__(AgentCogCommands)
        guild = MagicMock(spec=discord.Guild)
        guild.me = NS(name="Fubuki")
        cog.config = {guild: config}
        cog.bot = NS(is_owner=is_owner)
        for argument in (None, "imagegen", "responder", "missing", "full"):
            exported = []
            message = event().message

            async def capture_file(*args, exported=exported, message=message, **kwargs):
                if "file" in kwargs:
                    exported.append(kwargs["file"].fp.read())
                return message

            ctx = MagicMock(spec=commands.Context)
            ctx.guild = guild
            ctx.send = AsyncMock(side_effect=capture_file, return_value=message)
            ctx.embed_color = AsyncMock(return_value=discord.Color.blue())
            ctx.bot_permissions = NS(manage_messages=True)
            ctx.message = NS(id=1, delete=AsyncMock())
            await AgentCogCommands.prompt_cmd.callback(cog, ctx, argument)
            kwargs = ctx.send.call_args.kwargs
            if argument == "full":
                self.assertEqual(exported, ["Start custom content end"])
            elif argument == "missing":
                self.assertIn("not found", ctx.send.call_args.args[0])
            else:
                view = kwargs["view"]
                self.addCleanup(view.stop)
                self.assertTrue(view.has_components_v2())
                self.assertNotIn("embed", kwargs)
                self.assertNotIn("custom content", json.dumps(view.to_components()))
                self.assertEqual(view.message, ctx.send.return_value)
                ctx.message.delete.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
