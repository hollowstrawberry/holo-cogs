"""Installed Redbot integration tests. No login, Discord sends or config writes."""

import asyncio
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

try:
    from redbot.core import commands
except ModuleNotFoundError as error:
    if error.name == "redbot":
        raise unittest.SkipTest("Installed Redbot dependencies are required for runtime tests") from error
    raise

# isort: split

import discord
from test_moss import box, server_profile

from agent.agent import AgentCog
from agent.schema import ImageGenParams as AgentParams
from agent.tools.arcenciel_image import ArcencielImageTool
from arcenciel.arcenciel import Arcenciel
from arcenciel.arcenciel_api import ArcEnCielAPI
from arcenciel.comfy import ComfyMetadataReader
from arcenciel.commands import ArcencielCommands
from arcenciel.moss import agent_settings, moss_options
from arcenciel.schema import ImageGenParams, ImageRegionalParams, SplitType
from arcenciel.views.image_actions import ImageActions
from arcenciel.views.modify import ModifyModal


class MossRuntime(unittest.IsolatedAsyncioTestCase):
    def setup_request(self) -> tuple[commands.Context, AgentCog, Arcenciel, NS]:
        ctx = MagicMock(spec=commands.Context)
        ctx.guild = NS(me=NS(id=99))
        ctx.author = MagicMock(spec=discord.Member)
        ctx.author.mention = "@test"
        ctx.author.display_name = "Test"
        ctx.author.display_avatar.url = "https://example.test/avatar.png"
        ctx.channel = MagicMock(spec=discord.TextChannel)
        ctx.channel.id, ctx.channel.nsfw = 20, False
        ctx.channel.permissions_for.return_value = NS(manage_messages=False)
        ctx.message = NS(id=1, jump_url="https://example.test/message", reference=None)
        ctx.reply = AsyncMock(return_value=NS(delete=AsyncMock()))
        ctx.send = AsyncMock()
        config = MagicMock()
        guild = NS(
            enabled_functions=NS(value=["arcenciel_image"]),
            prompt_keys=NS(value={"moss": "Editable guild instructions"}),
            image_prompt_key=NS(value="moss"),
            image_model_default=NS(value="moss"),
            generation_channel_mode=NS(value="blacklist"),
            generation_channels=NS(value=[]),
            channel_mode=NS(value="blacklist"),
            channels=NS(value=[]),
            backread_messages=NS(value=5),
        )
        config.__getitem__.return_value = guild
        config.tool_settings = NS(value={})
        agent = object.__new__(AgentCog)
        agent.config = config
        agent.currently_generating = set()
        agent.find_last_sd_generated_image_resolution = AsyncMock(return_value=(None, None))
        cog = object.__new__(Arcenciel)
        cog.config = MagicMock()
        for key, value in {
            "negative_prompt": "old booru tags",
            "checkpoint": "anima.safetensors",
            "vae": "old-vae",
            "sampling_steps": 24,
            "cfg": 5,
            "sampler": "euler_ancestral",
            "scheduler": "beta",
            "width": 832,
            "height": 1216,
            "adetailer": True,
            "loading_emoji": "...",
        }.items():
            setattr(cog.config, key, AsyncMock(return_value=value))
        cog.config.user.return_value.checkpoint = AsyncMock(return_value="user-anima.safetensors")
        cog.config.guild.return_value.enabled = AsyncMock(return_value=True)
        cog.api = NS(
            refresh_moss_profile=AsyncMock(return_value=server_profile()),
            preflight_image=AsyncMock(),
            find_lora_entries=AsyncMock(return_value=[]),
            request_image=AsyncMock(return_value={"id": "test-job"}),
        )
        cog.queued_images = {}
        cog.check_quota = AsyncMock(return_value=True)
        cog.contains_blacklisted_word = AsyncMock(return_value=False)
        bot = NS(
            get_cog=lambda name: {"Arcenciel": cog, "AgentCog": agent}.get(name),
            get_embed_color=AsyncMock(return_value=discord.Color.blue()),
            user=NS(id=99),
        )
        ctx.bot = cog.bot = agent.bot = bot
        return ctx, agent, cog, guild

    async def finish_queued(self, cog: Arcenciel) -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        for gen in cog.queued_images.values():
            if gen.callback:
                await gen.callback
                gen.callback = None

    async def test_tool_uses_builtin_prompt_key_and_dynamic_profile(self) -> None:
        ctx, agent, cog, guild = self.setup_request()
        result = await ArcencielImageTool(ctx, agent).run({"prompt": "Two friends in a garden", "boxes": [box()]})
        await self.finish_queued(cog)
        self.assertIn("result", result)
        payload = cog.api.request_image.call_args.args[0]
        self.assertEqual(payload["modelName"], "renamed-sft.safetensors")
        self.assertEqual((payload["steps"], payload["cfg"], payload["moss"]["shift"]), (26, 3.5, 6))
        self.assertEqual(payload["negativePrompt"], "")
        self.assertNotIn("adetailer", payload)
        self.assertFalse(agent.currently_generating)
        self.assertEqual(ArcencielImageTool.schema.function.name, "generate_stable_diffusion")
        self.assertEqual(guild.prompt_keys.value, {"moss": "Editable guild instructions"})
        cog.config.negative_prompt.assert_not_awaited()

    async def test_actual_legacy_text_fallback_uses_same_profile(self) -> None:
        ctx, agent, cog, _ = self.setup_request()
        await AgentCog.generate_stable_diffusion(agent, ctx, "A cat sleeping in a sunbeam.")
        await self.finish_queued(cog)
        payload = cog.api.request_image.call_args.args[0]
        self.assertEqual(
            (payload["modelName"], payload["width"], payload["cfg"]), ("renamed-sft.safetensors", 768, 3.5)
        )
        self.assertFalse(agent.currently_generating)

    async def test_selected_custom_prompt_key_enables_both_agent_paths(self) -> None:
        ctx, agent, cog, guild = self.setup_request()
        guild.prompt_keys.value = {"imagegen": "Creator instructions"}
        guild.image_prompt_key.value = "imagegen"
        self.assertIn("result", await ArcencielImageTool(ctx, agent).run({"prompt": "A cat"}))
        await self.finish_queued(cog)
        cog.api.request_image.assert_awaited_once()

        ctx, agent, cog, guild = self.setup_request()
        guild.prompt_keys.value = {"imagegen": "Creator instructions"}
        guild.image_prompt_key.value = "imagegen"
        await AgentCog.generate_stable_diffusion(agent, ctx, "A cat")
        await self.finish_queued(cog)
        cog.api.request_image.assert_awaited_once()

    async def test_agent_default_and_explicit_checkpoint_switch(self) -> None:
        ctx, agent, cog, guild = self.setup_request()
        guild.image_model_default.value = "anima"
        result = await ArcencielImageTool(ctx, agent).run({"prompt": "A traveler in a forest"})
        self.assertIn("result", result)
        await self.finish_queued(cog)
        payload = cog.api.request_image.call_args.args[0]
        self.assertEqual((payload["modelName"], payload["steps"], payload["cfg"]), ("anima.safetensors", 30, 4))
        self.assertNotIn("moss", payload)
        cog.api.preflight_image.assert_awaited()

        ctx, agent, cog, guild = self.setup_request()
        guild.image_model_default.value = "moss"
        result = await ArcencielImageTool(ctx, agent).run({
            "prompt": "A traveler in a forest", "model_family": "illustrious", "checkpoint": "noob.safetensors",
        })
        self.assertIn("result", result)
        await self.finish_queued(cog)
        payload = cog.api.request_image.call_args.args[0]
        self.assertEqual(payload["modelName"], "noob.safetensors")
        self.assertNotIn("moss", payload)

    async def test_lora_infers_base_and_cross_base_is_accepted(self) -> None:
        ctx, agent, cog, _ = self.setup_request()
        cog.api.find_lora_entries.return_value = [
            {"name": "character.safetensors", "baseModel": "NoobAI Eps", "modelTitle": "Character"}
        ]
        result = await ArcencielImageTool(ctx, agent).run({
            "prompt": "A character", "loras": [{"name": "Character", "weight": 0.8}],
        })
        self.assertIn("result", result)
        await self.finish_queued(cog)
        payload = cog.api.request_image.call_args.args[0]
        self.assertEqual(payload["modelName"], "noob.safetensors")
        self.assertEqual(payload["loras"], [{"name": "character.safetensors", "weight": 0.8}])

        ctx, agent, cog, _ = self.setup_request()
        cog.api.find_lora_entries.return_value = [
            {"name": "illus-character.safetensors", "baseModel": "Illustrious 1.0", "modelTitle": "Character"}
        ]
        result = await ArcencielImageTool(ctx, agent).run({
            "prompt": "A character", "checkpoint": "noob.safetensors", "loras": [{"name": "Character"}],
        })
        self.assertIn("result", result)
        await self.finish_queued(cog)
        self.assertEqual(cog.api.request_image.call_args.args[0]["loras"][0]["name"], "illus-character.safetensors")

    async def test_preflight_failure_never_queues(self) -> None:
        ctx, agent, cog, _ = self.setup_request()
        cog.api.preflight_image.side_effect = ValueError("Incompatible LoRA")
        result = await ArcencielImageTool(ctx, agent).run({"prompt": "A cat"})
        self.assertIn("Incompatible LoRA", result)
        cog.api.request_image.assert_not_awaited()
        self.assertFalse(agent.currently_generating)

    async def test_missing_prompt_disabled_tool_and_channel_policy_block_both_paths(self) -> None:
        for problem in ("prompt", "disabled", "channel"):
            with self.subTest(problem=problem):
                ctx, agent, cog, guild = self.setup_request()
                if problem == "prompt":
                    guild.prompt_keys.value = {}
                elif problem == "disabled":
                    guild.enabled_functions.value = []
                else:
                    guild.generation_channels.value = [ctx.channel.id]
                result = await ArcencielImageTool(ctx, agent).run({"prompt": "A cat"})
                self.assertIn("<error>", result)
                await AgentCog.generate_stable_diffusion(agent, ctx, "A cat")
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                cog.api.request_image.assert_not_awaited()
                self.assertFalse(agent.currently_generating)
        ctx, agent, _, guild = self.setup_request()
        guild.generation_channels.value = [ctx.channel.id]
        ctx.channel.permissions_for.return_value.manage_messages = True
        self.assertEqual(agent_settings(agent, ctx), {})

    async def test_invalid_models_and_boxes_do_not_queue(self) -> None:
        for args in [{"prompt": "A cat", "boxes": [box()] * 4}, {"prompt": "A cat", "boxes": None}]:
            ctx, agent, cog, _ = self.setup_request()
            self.assertIn("<error>", await ArcencielImageTool(ctx, agent).run(args))
            cog.api.request_image.assert_not_awaited()
            self.assertFalse(agent.currently_generating)
        ctx, agent, cog, _ = self.setup_request()
        agent.config.tool_settings.value = {"checkpoint": "removed.safetensors"}
        self.assertIn("<error>", await ArcencielImageTool(ctx, agent).run({"prompt": "A cat"}))
        cog.api.request_image.assert_not_awaited()

    async def test_early_exits_release_callback_and_moderate_box_text(self) -> None:
        for failure in ("guild", "quota", "blacklist", "profile", "timeout"):
            with self.subTest(failure=failure):
                ctx, _, cog, _ = self.setup_request()
                callback = AsyncMock()
                if failure == "guild":
                    cog.config.guild.return_value.enabled.return_value = False
                elif failure == "quota":
                    cog.check_quota.return_value = False
                elif failure == "blacklist":
                    cog.contains_blacklisted_word.return_value = True
                elif failure == "timeout":
                    cog.api.refresh_moss_profile.side_effect = TimeoutError("Discovery timed out")
                else:
                    cog.api.refresh_moss_profile.side_effect = ValueError("Unavailable profile")
                await cog.generate_image(
                    ctx,
                    params=AgentParams(prompt="Scene", moss=moss_options([box()], max_boxes=3)),
                    callback=callback(),
                )
                callback.assert_awaited_once()
                cog.api.request_image.assert_not_awaited()
                if failure == "blacklist":
                    self.assertIn("A woman in red", cog.contains_blacklisted_word.call_args.args[0])

    async def test_explicit_legacy_commands_keep_settings(self) -> None:
        ctx, _, cog, _ = self.setup_request()
        payload = await cog.build_image_payload(ImageGenParams(prompt="legacy prompt"), ctx.author, False)
        self.assertEqual(payload["modelName"], "user-anima.safetensors")
        self.assertEqual(payload["cfg"], 5)
        self.assertIn("adetailer", payload)
        cog.api.refresh_moss_profile.assert_not_awaited()

    async def test_legacy_regional_masks_keep_geometry_and_paths(self) -> None:
        from io import BytesIO

        from PIL import Image

        ctx, _, cog, _ = self.setup_request()
        cog.api.upload_image = AsyncMock(side_effect=["left.png", "right.png"])
        await cog.generate_image(
            ctx,
            params=ImageGenParams(
                prompt="Two people",
                width=64,
                height=64,
                regions=ImageRegionalParams("Woman in red", "Man in blue", SplitType.HORIZONTAL.value, 50),
            ),
        )
        await self.finish_queued(cog)
        payload = cog.api.request_image.call_args.args[0]
        self.assertEqual(payload["modelName"], "user-anima.safetensors")
        regions = payload["attentionCouple"]["regions"]
        self.assertEqual([region["maskPath"] for region in regions], ["left.png", "right.png"])
        self.assertEqual([region["prompt"] for region in regions], ["Woman in red", "Man in blue"])
        masks = [Image.open(BytesIO(call.args[0])).getchannel("A") for call in cog.api.upload_image.call_args_list]
        self.assertEqual([image.size for image in masks], [(64, 64), (64, 64)])
        self.assertEqual([image.getpixel((16, 32)) for image in masks], [0, 255])
        self.assertEqual([image.getpixel((48, 32)) for image in masks], [255, 0])
        cog.api.refresh_moss_profile.assert_not_awaited()

    async def test_missing_cog_and_nontext_arguments_fail_without_queueing(self) -> None:
        ctx, agent, cog, _ = self.setup_request()
        for argument in ("prompt", "negative_prompt", "resolution", "existing"):
            with self.subTest(argument=argument):
                result = await ArcencielImageTool(ctx, agent).run({"prompt": "A cat", argument: []})
                self.assertIn("<error>", result)
        ctx.bot.get_cog = lambda name: None
        self.assertIn("cog not installed", await ArcencielImageTool(ctx, agent).run({"prompt": "A cat"}))
        cog.api.request_image.assert_not_awaited()

    async def test_slash_command_uses_shared_defaults_and_permissions(self) -> None:
        interaction = NS(response=NS(defer=AsyncMock()), followup=NS(send=AsyncMock()))
        cog = NS(
            bot=NS(get_context=AsyncMock()), can_run_command=AsyncMock(return_value=True), generate_image=AsyncMock()
        )
        await ArcencielCommands.moss_app.callback(
            cog, interaction, "Cafe", left="Woman in red", right="Man in blue", title="HELLO MOSS"
        )
        params = cog.generate_image.call_args.kwargs["params"]
        self.assertEqual(len(params.moss["layout"]["items"]), 3)
        self.assertIsNone(params.checkpoint)
        self.assertIsNone(params.steps)
        self.assertIsNone(params.width)
        self.assertEqual(cog.can_run_command.call_args.args[1], "txt2img")
        cog.can_run_command.return_value = False
        cog.generate_image.reset_mock()
        await ArcencielCommands.moss_app.callback(cog, interaction, "Cafe")
        cog.generate_image.assert_not_awaited()

    async def test_actions_preserve_layout_and_hide_unsupported_buttons(self) -> None:
        ctx, _, cog, _ = self.setup_request()
        cog.generate_image = AsyncMock()
        cog.autocomplete_cache = {}
        payload = await cog.build_image_payload(
            ImageGenParams(prompt="Natural scene", moss=moss_options([box()], max_boxes=3)), ctx.author, False
        )
        metadata = NS(as_dict=lambda: {"Prompt": '{"summary":{}}', "Negative Prompt": "", "Seed": "42"}, raw="{}")
        view = ImageActions(cog, metadata, payload, ctx.author, ctx.channel, 2048)
        self.assertNotIn(view.button_variation, view.children)
        self.assertNotIn(view.button_upscale, view.children)
        interaction = NS(response=NS(defer=AsyncMock()), user=NS(mention="@test"))
        await view.reroll_image(interaction)
        reroll = cog.generate_image.call_args.kwargs["payload"]
        self.assertEqual(reroll["moss"], payload["moss"])
        self.assertNotIn("extraSeedStrength", reroll)
        modal = ModifyModal(view)
        self.assertEqual(modal.prompt_edit.component.default, "Natural scene")
        modal.prompt_edit.component._value = "Updated scene"
        modal.negative_prompt_edit.component._value = ""
        modal.seed_checkbox.component._value = False
        await modal.on_submit(interaction)
        modified = cog.generate_image.call_args.kwargs["payload"]
        self.assertEqual(modified["prompt"], "Updated scene")
        self.assertEqual(modified["seed"], 42)
        self.assertEqual(modified["moss"], payload["moss"])
        self.assertNotIn("extraSeedStrength", modified)

    async def test_existing_png_recovers_layout_and_explicit_empty_boxes_clear_it(self) -> None:
        from io import BytesIO

        from PIL import Image, PngImagePlugin

        layout = {
            "summary": {"caption": "A poster without watermark"},
            "bbox_coord_space": "abs_centralized",
            "bbox_format": "yxyx",
            "items": [{"label": "text", "caption": "Title", "text": "HELLO\nMOSS", "bbox": [-500, -500, -250, 500]}],
        }
        graph = {
            "11": {"class_type": "ArcEnCielMossTextEncode", "inputs": {"text": json.dumps(layout)}},
            "12": {"class_type": "ArcEnCielMossTextEncode", "inputs": {"text": "low quality"}},
            "19": {
                "class_type": "KSampler",
                "inputs": {
                    "steps": 26,
                    "cfg": 4,
                    "seed": 42,
                    "sampler_name": "euler",
                    "scheduler": "moss",
                    "positive": ["11", 0],
                    "negative": ["12", 0],
                },
            },
        }
        info = PngImagePlugin.PngInfo()
        info.add_text("prompt", json.dumps(graph))
        data = BytesIO()
        Image.new("RGB", (1000, 1000)).save(data, format="PNG", pnginfo=info)
        metadata = ComfyMetadataReader.from_bytes(data.getvalue()).as_dict()
        self.assertEqual(json.loads(metadata["Prompt"]), layout)
        self.assertEqual(metadata["Negative Prompt"], "low quality")
        for overrides in ({}, {"boxes": []}):
            ctx, agent, cog, _ = self.setup_request()
            tool = ArcencielImageTool(ctx, agent)
            tool.find_attachment = AsyncMock(
                return_value=(
                    True,
                    NS(attachments=[NS(filename="image.png", read=AsyncMock(return_value=data.getvalue()))]),
                )
            )
            result = await tool.run({"prompt": "A new poster", "existing": "image.png", **overrides})
            self.assertIn("result", result)
            await self.finish_queued(cog)
            payload = cog.api.request_image.call_args.args[0]
            self.assertEqual(payload["seed"], 42)
            if overrides:
                self.assertNotIn("layout", payload["moss"])
            else:
                recovered = payload["moss"]["layout"]["items"][0]
                self.assertEqual(recovered["text"], "HELLO\nMOSS")
                self.assertEqual(recovered["bbox"], [0, 0, 250, 1000])

    async def test_autocomplete_reuses_discovered_sft_choices(self) -> None:
        _, catalog = server_profile()
        cog = NS(
            api=NS(model_catalog=catalog, refresh_moss_profile=AsyncMock()),
            build_autocomplete_choices=AsyncMock(return_value=[]),
        )
        await ArcencielCommands.moss_checkpoint_autocomplete(cog, object(), "sft")
        choices = cog.build_autocomplete_choices.call_args.args[1]
        self.assertEqual(list(choices.values()), ["renamed-sft.safetensors"])
        cog.api.refresh_moss_profile.assert_not_awaited()

    async def test_api_snapshot_and_compression(self) -> None:
        client = ArcEnCielAPI(NS(), "https://example.test/api", "test-key")
        self.assertEqual(client.session.headers["Accept-Encoding"], "gzip, deflate")
        await client.session.close()
        options, catalog = server_profile()

        def response(data: object, status: int = 200) -> MagicMock:
            item = MagicMock()
            item.status = status
            item.json = AsyncMock(return_value=data)
            item.__aenter__ = AsyncMock(return_value=item)
            return item

        client.session = NS(get=MagicMock(side_effect=[response(options), response(catalog)]))
        self.assertEqual(await client.refresh_moss_profile(), (options, catalog))
        client.session.get.side_effect = [response({"new": "options"}), response({"error": "Unavailable"}, 503)]
        with self.assertRaisesRegex(Exception, "Unavailable"):
            await client.refresh_moss_profile()
        self.assertEqual((client.generator_options, client.model_catalog), (options, catalog))
        client.session.get.side_effect = [response(options), response([])]
        with self.assertRaisesRegex(ValueError, "JSON object"):
            await client.refresh_moss_profile()
        self.assertEqual((client.generator_options, client.model_catalog), (options, catalog))

    def test_comfy_layout_is_not_mistaken_for_negative_prompt(self) -> None:
        layout = json.dumps(
            {
                "summary": {"caption": "A worst quality sign"},
                "items": [],
                "bbox_coord_space": "abs_centralized",
                "bbox_format": "yxyx",
            }
        )
        self.assertTrue(ComfyMetadataReader.is_useful_text_candidate(layout))
        self.assertFalse(ComfyMetadataReader.looks_negative(layout))


if __name__ == "__main__":
    unittest.main()
