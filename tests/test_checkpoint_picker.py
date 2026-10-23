"""Focused Redbot runtime checks; no Discord login, API calls or config writes."""

import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from agent.tools.arcenciel_image import ArcencielImageTool
from arcenciel.arcenciel import Arcenciel
from arcenciel.settings import ArcencielSettings
from arcenciel.views.checkpoint_select import (
    CheckpointSearchModal,
    CheckpointSelectView,
    checkpoints_from_catalog,
    exact_checkpoint,
)
from redbot.core import commands


def entry(name, base="Anima", **extra):
    return {"name": name + ".safetensors", "baseModel": base, "storageState": "loaded", **extra}


def profile():
    return (
        {
            "limits": {
                "moss": {
                    "available": True,
                    "modes": ["txt2img"],
                    "coordinateSpace": "normalized_1000",
                    "maxBoxes": 3,
                    "defaults": {
                        "steps": 24,
                        "cfg": 3.5,
                        "shift": 6,
                        "samplerName": "euler",
                        "scheduler": "moss",
                        "width": 1024,
                        "height": 1024,
                    },
                },
                "anima": {"defaults": {"modelName": "anima-a.safetensors"}},
            }
        },
        {
            "checkpoints": [
                entry("anima-a"),
                entry("anima-b"),
                entry("illus", "Illustrious"),
                entry("noob", "NoobAI Eps"),
                entry("pony", "Pony"),
                entry("moss", "Moss SFT", modelCapabilities=["moss", "moss_sft"]),
                entry("moss-base", "Moss Base", modelCapabilities=["moss"]),
            ]
        },
    )


def setup_request(personal="anima-b.safetensors"):
    ctx = MagicMock(spec=commands.Context)
    ctx.author = MagicMock(spec=discord.Member)
    ctx.author.id, ctx.author.display_name, ctx.author.mention = 1, "Tester", "@tester"
    ctx.channel = MagicMock(spec=discord.TextChannel)
    ctx.guild = NS(me=NS(id=99))
    ctx.message = NS(id=10, jump_url="https://example.test/message")
    ctx.send = AsyncMock(return_value=NS(edit=AsyncMock()))
    cog = object.__new__(Arcenciel)
    cog.config = MagicMock()
    cog.config.user.return_value.checkpoint = AsyncMock(return_value=personal)
    cog.config.user.return_value.checkpoint.set = AsyncMock()
    cog.config.checkpoint = AsyncMock(return_value="anima-a.safetensors")
    cog.api = NS(refresh_moss_profile=AsyncMock(return_value=profile()), find_lora_entries=AsyncMock(return_value=[]))
    cog.prepare_image_payload = AsyncMock(return_value={})

    async def generate(*args, **kwargs):
        await kwargs["callback"]

    cog.generate_image = AsyncMock(side_effect=generate)
    bot = NS(
        get_cog=lambda name: cog if name == "Arcenciel" else None,
        get_embed_color=AsyncMock(return_value=discord.Color.blue()),
    )
    cog.bot = ctx.bot = bot
    agent = NS(bot=bot, config=NS(tool_settings=NS(value={"checkpoint": "moss"})), currently_generating=set())
    return ctx, cog, agent


def interaction(user_id=1):
    return NS(
        user=NS(id=user_id),
        response=NS(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock(), send_modal=AsyncMock()),
        followup=NS(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


class CheckpointPickerTests(unittest.IsolatedAsyncioTestCase):
    def make_view(self, catalog=None):
        ctx, cog, _ = setup_request()
        entries = checkpoints_from_catalog(catalog or profile()[1])
        return cog, CheckpointSelectView(
            cog, ctx.author, entries, "anima-b.safetensors", "anima-a.safetensors", discord.Color.blue()
        )

    async def test_catalog_keeps_colliding_names_and_exact_resolution(self):
        entries = checkpoints_from_catalog(
            {"checkpoints": [entry("100_200_Same"), entry("300_400_Same"), entry("hidden", storageState="remote")]}
        )
        self.assertEqual(len(entries), 2)
        self.assertIsNone(exact_checkpoint(entries, "Same"))
        self.assertEqual(exact_checkpoint(entries, "100_200_same").name, "100_200_Same.safetensors")
        single = checkpoints_from_catalog({"checkpoints": [entry("100_200_Hoseki")]})
        for query in ("Hoseki", "hoseki.safetensors", "100_200_Hoseki", "`100_200_Hoseki.safetensors`"):
            self.assertEqual(exact_checkpoint(single, query), single[0])
        self.assertIsNone(exact_checkpoint(single, "Hose"))

    async def test_all_pages_search_base_filter_and_discord_limits(self):
        catalog = {"checkpoints": [entry(f"model-{n:02}", "Anima" if n % 2 else "Pony") for n in range(73)]}
        _, view = self.make_view(catalog)
        keys = set()
        for page in range(4):
            self.assertEqual(view.page, page)
            self.assertLessEqual(len(view.checkpoint_select.options), 20)
            keys.update(option.value for option in view.checkpoint_select.options)
            self.assertLessEqual(len(view.embed().description), 4096)
            await view.next.callback(interaction())
        self.assertEqual(len(keys), 73)
        self.assertTrue(view.next.disabled)
        view.base_select._values = [
            next(option.value for option in view.base_select.options if option.label == "Anima")
        ]
        await view.base_select.callback(interaction())
        self.assertEqual(len(view.filtered()), 36)
        self.assertEqual(view.page, 0)
        modal = CheckpointSearchModal(view)
        modal.query._value = "model-01"
        await modal.on_submit(interaction())
        self.assertEqual([item.name for item in view.filtered()], ["model-01.safetensors"])
        await view.clear_filters(interaction())
        self.assertEqual(len(view.filtered()), 73)

    async def test_long_names_and_collisions_have_distinct_short_values(self):
        _, view = self.make_view({"checkpoints": [entry("100_200_Same"), entry("300_400_Same"), entry("x" * 200)]})
        options = view.checkpoint_select.options
        self.assertEqual(len({option.value for option in options}), 3)
        self.assertEqual(len({option.label for option in options}), 3)
        self.assertTrue(all(len(option.value) <= 100 and len(option.label) <= 100 for option in options))

    async def test_selection_revalidates_and_saves_raw_filename_and_reset(self):
        cog, view = self.make_view()
        selected = next(item for item in view.entries if item.name == "noob.safetensors")
        view.checkpoint_select._values = [selected.key]
        await view.checkpoint_select.callback(interaction())
        cog.api.refresh_moss_profile.assert_awaited_once()
        cog.config.user.return_value.checkpoint.set.assert_awaited_once_with(selected.name)
        self.assertEqual(view.current, selected.name)
        await view.reset.callback(interaction())
        cog.config.user.return_value.checkpoint.set.assert_awaited_with("")
        self.assertIn("Commands: anima-a", view.embed().fields[0].value)

    async def test_removed_model_or_api_failure_does_not_write(self):
        for removed in (True, False):
            cog, view = self.make_view()
            view.checkpoint_select._values = [view.entries[0].key]
            if removed:
                cog.api.refresh_moss_profile.return_value = ({}, {"checkpoints": []})
            else:
                cog.api.refresh_moss_profile.side_effect = TimeoutError()
            event = interaction()
            await view.select_checkpoint(event)
            cog.config.user.return_value.checkpoint.set.assert_not_awaited()
            event.followup.send.assert_awaited_once()

    async def test_other_users_and_expired_modal_cannot_change_selection(self):
        cog, view = self.make_view()
        self.assertFalse(await view.interaction_check(interaction(2)))
        modal = CheckpointSearchModal(view)
        view.message = NS(edit=AsyncMock())
        view.stop()
        await view.on_timeout()
        self.assertTrue(all(item.disabled for item in view.children))
        modal.query._value = "anima"
        await modal.on_submit(interaction())
        self.assertEqual(view.query, "")
        cog.config.user.return_value.checkpoint.set.assert_not_awaited()

    async def test_text_command_accepts_names_or_opens_filtered_picker(self):
        for query, saves in (("ANIMA-B", True), ("anima-b.safetensors", True), ("anima", False), (None, False)):
            ctx, cog, _ = setup_request()
            await ArcencielSettings.member_checkpoint.callback(cog, ctx, checkpoint=query)
            self.assertEqual(cog.config.user.return_value.checkpoint.set.await_count, int(saves))
            ctx.send.assert_awaited_once()
            if not saves:
                view = ctx.send.call_args.kwargs["view"]
                self.assertEqual(len(view.filtered()), 2 if query else 7)
                view.stop()

    async def test_command_failure_preserves_default_and_reset_works_offline(self):
        ctx, cog, _ = setup_request()
        cog.api.refresh_moss_profile.side_effect = TimeoutError()
        await ArcencielSettings.member_checkpoint.callback(cog, ctx)
        cog.config.user.return_value.checkpoint.set.assert_not_awaited()
        self.assertIn("not changed", ctx.send.call_args.args[0])
        cog.api = None
        await ArcencielSettings.member_checkpoint.callback(cog, ctx, checkpoint="reset")
        cog.config.user.return_value.checkpoint.set.assert_awaited_once_with("")


class AgentCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def run_tool(self, personal, arguments=None, lora_base=None, inherited=None):
        ctx, cog, agent = setup_request(personal)
        tool = ArcencielImageTool(ctx, agent)
        args = {"prompt": "A cat", **(arguments or {})}
        if lora_base:
            args["prompt"] += " <lora:style:1>"
            cog.api.find_lora_entries.return_value = [{"name": "style.safetensors", "baseModel": lora_base}]
        if inherited:
            args["existing"] = "image.png"
            tool.find_attachment = AsyncMock(
                return_value=(True, NS(attachments=[NS(filename="image.png", read=AsyncMock(return_value=b"png"))]))
            )
        with patch(
            "arcenciel.comfy.ComfyMetadataReader.from_bytes", return_value=NS(as_dict=lambda: {"Checkpoint": inherited})
        ):
            result = await tool.run(args)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertIn("result", result)
        return cog.prepare_image_payload.call_args.args[1], result, cog

    async def test_personal_default_and_reset_to_tool_default(self):
        for personal, expected in (("anima-b.safetensors", "anima-b.safetensors"), ("", "moss.safetensors")):
            params, _, _ = await self.run_tool(personal)
            self.assertEqual(params.checkpoint, expected)

    async def test_explicit_checkpoint_and_existing_image_win(self):
        for arguments, inherited in (({"checkpoint": "anima"}, None), ({}, "anima-a.safetensors")):
            params, _, cog = await self.run_tool("anima-b.safetensors", arguments, inherited=inherited)
            self.assertEqual(params.checkpoint, "anima-a.safetensors")
            cog.config.user.return_value.checkpoint.assert_not_awaited()
        params, _, _ = await self.run_tool("anima-b.safetensors", {"checkpoint": "noobai"})
        self.assertEqual(params.checkpoint, "noob.safetensors")

    async def test_lora_compatibility_keeps_same_family_preference(self):
        for personal, lora_base, expected in (
            ("anima-b.safetensors", "Anima", "anima-b.safetensors"),
            ("illus.safetensors", "NoobAI Eps", "illus.safetensors"),
            ("noob.safetensors", "Illustrious", "noob.safetensors"),
            ("anima-b.safetensors", "NoobAI Eps", "noob.safetensors"),
            ("moss-base.safetensors", "Moss SFT", "moss.safetensors"),
        ):
            params, _, _ = await self.run_tool(personal, lora_base=lora_base)
            self.assertEqual(params.checkpoint, expected)
            self.assertEqual(params.loras, ["<lora:style:1>"])

    async def test_unavailable_or_unsupported_default_falls_back_with_warning(self):
        for personal in ("removed.safetensors", "pony.safetensors"):
            params, result, _ = await self.run_tool(personal)
            self.assertEqual(params.checkpoint, "moss.safetensors")
            self.assertIn("unavailable or unsupported", str(result))

    async def test_personal_non_moss_default_does_not_break_boxes(self):
        params, result, _ = await self.run_tool(
            "anima-b.safetensors", {"boxes": [{"label": "object", "caption": "Cat", "bbox": [0, 0, 1000, 1000]}]}
        )
        self.assertEqual(params.checkpoint, "moss.safetensors")
        self.assertIn("MOSS boxes require a MOSS checkpoint", str(result))


if __name__ == "__main__":
    unittest.main()
