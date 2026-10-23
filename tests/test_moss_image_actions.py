"""The generated-image entry point keeps generation state intact."""

import json
import unittest
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord
from arcenciel.comfy import ComfyMetadataReader
from arcenciel.views.image_actions import ImageActions
from test_moss_metadata import graph, interaction, png


class ImageActionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_and_lazy_layout_preserve_payload(self):
        metadata = ComfyMetadataReader.from_info({"prompt": json.dumps(graph())}, 200, 100)
        payload = {"moss": {}, "seed": 123, "prompt": "Original request"}
        original = deepcopy(payload)
        cog = NS(
            bot=NS(get_embed_color=AsyncMock(return_value=discord.Color.teal())),
            config=NS(arcenciel_emoji=AsyncMock(return_value="AEC")),
            autocomplete_cache={},
            generate_image=AsyncMock(),
            resolve_arcenciel_resources=AsyncMock(return_value=["resource"]),
        )
        view = ImageActions(cog, metadata, payload, NS(id=5), NS(), 2048)
        attachment = NS(is_spoiler=lambda: True, read=AsyncMock(return_value=png()))
        view.message = NS(attachments=[attachment])
        event = interaction()
        await view.get_caption(event)
        args = event.followup.send.call_args.kwargs
        self.assertTrue(args["ephemeral"])
        self.assertEqual(args["embed"].fields[0].name, "Scene · MOSS")
        self.assertNotIn("bbox_coord_space", str(args["embed"].to_dict()))
        self.assertIn("View layout", [button.label for button in args["view"].children])
        self.assertEqual(args["view"].raw_metadata, metadata.raw)
        attachment.read.assert_not_awaited()
        await args["view"].view_layout(event)
        self.assertTrue(event.followup.send.call_args.kwargs["file"].spoiler)
        self.assertEqual(payload, original)
        self.assertEqual(metadata.as_dict()["Prompt"], metadata.moss_prompt)
        cog.generate_image.assert_not_awaited()
        event.followup.send.call_args.kwargs["view"].stop()
        args["view"].stop()
        view.stop()

    async def test_non_moss_keeps_existing_view(self):
        metadata = ComfyMetadataReader.from_info({"prompt": json.dumps(graph("ordinary prompt"))}, 200, 100)
        cog = NS(
            bot=NS(get_embed_color=AsyncMock(return_value=discord.Color.teal())),
            config=NS(),
            autocomplete_cache={},
            generate_image=AsyncMock(),
            resolve_arcenciel_resources=AsyncMock(return_value=[]),
        )
        view = ImageActions(cog, metadata, {"width": 200, "height": 100}, NS(id=5), NS(), 2048)
        event = interaction()
        await view.get_caption(event)
        args = event.followup.send.call_args.kwargs
        self.assertEqual([button.label for button in args["view"].children], ["View Full"])
        self.assertEqual(args["embed"].fields[0].value, "ordinary prompt")
        args["view"].stop()
        view.stop()
