"""Integration tests using installed Redbot/Discord dependencies; never logs in or sends messages."""

import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
from agent.tools.arcenciel_image import ArcencielImageTool
from arcenciel.commands import ArcencielCommands
from arcenciel.moss import MOSS_SFT, moss_options
from arcenciel.views.image_actions import ImageActions
from arcenciel.views.modify import ModifyModal


class MossRuntime(unittest.IsolatedAsyncioTestCase):
    def context(self, blocked=False):
        guild = NS(me=NS(id=99))
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 20
        channel.permissions_for.return_value = NS(manage_messages=False)
        author = MagicMock(spec=discord.Member)
        author.mention = "@test"
        arcenciel = NS(generate_image=AsyncMock())
        ctx = NS(
            guild=guild,
            author=author,
            channel=channel,
            message=NS(id=1, jump_url="https://example.test/message"),
            bot=NS(get_cog=lambda name: arcenciel if name == "Arcenciel" else None),
        )
        config = MagicMock()
        config.__getitem__.return_value = NS(
            generation_channel_mode=NS(value="blacklist"), generation_channels=NS(value=[20] if blocked else [])
        )
        config.tool_settings = NS(
            value={"checkpoint": "anima.safetensors", "sampler": "euler_ancestral", "scheduler": "beta"}
        )
        cog = NS(config=config, currently_generating=set())
        return ctx, cog, arcenciel

    async def test_agent_uses_one_moss_tool_and_ignores_old_anima_settings(self):
        ctx, cog, arcenciel = self.context()

        async def completed(*args, **kwargs):
            await kwargs["callback"]

        arcenciel.generate_image.side_effect = completed
        result = await ArcencielImageTool(ctx, cog).run(
            {
                "prompt": "A cat at a cafe.",
                "boxes": [{"label": "text", "caption": "Title", "text": "HELLO\nMOSS", "bbox": [0, 0, 200, 1000]}],
            }
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertIn("result", result)
        params = arcenciel.generate_image.call_args.kwargs["params"]
        self.assertEqual(
            (params.checkpoint, params.steps, params.cfg, params.sampler, params.scheduler),
            (MOSS_SFT, 28, 4, "euler", "moss"),
        )
        self.assertEqual(params.moss["layout"]["items"][0]["text"], "HELLO\nMOSS")
        self.assertEqual(ArcencielImageTool.schema.function.name, "generate_moss_image")
        self.assertEqual(ArcencielImageTool.display_name, "arcenciel_image")
        self.assertFalse(cog.currently_generating)

    async def test_invalid_boxes_and_channel_restrictions_never_queue(self):
        for blocked, args in [(True, {"prompt": "Cat"}), (False, {"prompt": "Cat", "boxes": [{"bbox": [0, 0, 1, 1]}]})]:
            ctx, cog, arcenciel = self.context(blocked)
            result = await ArcencielImageTool(ctx, cog).run(args)
            self.assertIn("<error>", result)
            arcenciel.generate_image.assert_not_called()

    async def test_explicit_command_uses_existing_permissions_and_simple_boxes(self):
        interaction = NS(response=NS(defer=AsyncMock()), followup=NS(send=AsyncMock()))
        cog = NS(
            bot=NS(get_context=AsyncMock(return_value=object())),
            can_run_command=AsyncMock(return_value=True),
            generate_image=AsyncMock(),
        )
        await ArcencielCommands.moss_app.callback(
            cog, interaction, "Cafe", left="Woman in red", right="Man in blue", title="HELLO MOSS"
        )
        cog.can_run_command.assert_awaited_once()
        self.assertEqual(cog.can_run_command.call_args.args[1], "txt2img")
        params = cog.generate_image.call_args.kwargs["params"]
        self.assertEqual(len(params.moss["layout"]["items"]), 3)
        cog.can_run_command.return_value = False
        cog.generate_image.reset_mock()
        await ArcencielCommands.moss_app.callback(cog, interaction, "Cafe")
        cog.generate_image.assert_not_called()

    async def test_moss_actions_keep_recipe_hide_unsupported_buttons(self):
        payload = {
            "modelName": MOSS_SFT,
            "prompt": "Natural scene",
            "negativePrompt": "",
            "width": 1024,
            "height": 1024,
            "moss": moss_options([{"label": "text", "caption": "Title", "text": "MOSS", "bbox": [0, 0, 200, 1000]}]),
        }
        metadata = NS(as_dict=lambda: {"Prompt": '{"summary":{}}'}, raw="{}")
        cog = NS(
            bot=object(),
            config=object(),
            autocomplete_cache={},
            generate_image=AsyncMock(),
            resolve_arcenciel_resources=AsyncMock(),
        )
        view = ImageActions(cog, metadata, payload, MagicMock(spec=discord.Member), object(), 2048)
        self.assertIn(view.button_modify, view.children)
        self.assertNotIn(view.button_variation, view.children)
        self.assertNotIn(view.button_upscale, view.children)
        modal = ModifyModal(view)
        self.assertEqual(modal.prompt_edit.component.default, "Natural scene")
        self.assertEqual(modal.payload["moss"], payload["moss"])
        interaction = NS(response=NS(defer=AsyncMock()), user=NS(mention="@test"))
        await view.reroll_image(interaction)
        rerolled = cog.generate_image.call_args.kwargs["payload"]
        self.assertEqual(rerolled["moss"], payload["moss"])
        self.assertFalse(set(rerolled) & {"extraSeed", "extraSeedStrength"})
        view.stop()
        modal.stop()


if __name__ == "__main__":
    unittest.main()
