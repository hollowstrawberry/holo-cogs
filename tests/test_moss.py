"""Run with python -m unittest discover -s tests -p 'test_moss.py'.

Pure recipe tests and the actual payload-builder method with configuration fakes;
no Discord login, messages, external services or Redbot installation required.
"""

import ast
import importlib.util
import json
import re
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("moss_recipe", ROOT / "arcenciel/moss.py")
moss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(moss)


def params(**changes):
    values = dict(
        prompt="Two friends in a mountain cafe.",
        negative_prompt=None,
        width=None,
        height=None,
        cfg=None,
        sampler=None,
        scheduler=None,
        steps=None,
        seed=-1,
        variation=0,
        checkpoint=None,
        vae=None,
        loras=[],
        subseed=-1,
        subseed_strength=0,
        image=None,
        regions=None,
        moss=None,
    )
    return SimpleNamespace(**(values | changes))


def builder():
    # Execute the production method unchanged, isolating only optional Discord/Redbot imports.
    tree = ast.parse((ROOT / "arcenciel/arcenciel.py").read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Arcenciel")
    method = next(
        node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "build_image_payload"
    )
    namespace = dict(
        is_moss=moss.is_moss,
        build_moss_payload=moss.build_moss_payload,
        ImageGenParams=object,
        discord=SimpleNamespace(Member=object),
        deepcopy=deepcopy,
        constants=SimpleNamespace(
            LORA_PATTERN=re.compile(r"(<lora:([^:>]+):([0-9.]+)>)"), ADETAILER_ARGS={"enabled": True}
        ),
    )
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(ROOT / "arcenciel/arcenciel.py"), "exec"), namespace)
    return namespace["build_image_payload"]


class MossRecipes(unittest.TestCase):
    def test_text_whitespace_and_coordinates_round_trip(self):
        box = {"label": "text", "caption": "Large title", "text": " HELLO\nMOSS ", "bbox": [10, 20, 300, 980]}
        recipe = moss.moss_options([box])
        self.assertEqual(recipe["layout"]["items"][0], box)
        box["bbox"][0] = 900
        self.assertEqual(recipe["layout"]["items"][0]["bbox"][0], 10)
        self.assertEqual(moss.validate_options(recipe), recipe)

    def test_invalid_boxes_rejected(self):
        valid = {"label": "character", "caption": "Person", "bbox": [0, 0, 1000, 1000]}
        for patch in (
            {"bbox": [0, 0, 0, 1]},
            {"bbox": [0, 0, 1001, 1]},
            {"bbox": [False, 0, 10, 10]},
            {"caption": " "},
            {"caption": "x" * 2049},
            {"label": "text"},
            {"label": "unknown"},
            {"text": "not a text box"},
            {"maskPath": "/tmp/file"},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                moss.moss_options([valid | patch])
        with self.assertRaises(ValueError):
            moss.moss_options([valid] * 9)

    def test_shift_finite_range(self):
        for value in (True, None, "5", 0, 21, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                moss.moss_options(shift=value)

    def test_empty_boxes_mean_scene_only(self):
        self.assertEqual(moss.moss_options([]), {"shift": 5})

    def test_invalid_layout_version_or_unknown_field(self):
        valid = moss.moss_options([{"label": "object", "caption": "Cat", "bbox": [0, 0, 900, 900]}])
        for patch in ({"version": 2}, {"coordinateSpace": "pixels"}, {"items": []}, {"unknown": True}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                moss.validate_options({"layout": valid["layout"] | patch})

    def test_native_metadata_recovers_same_visual_coordinates(self):
        raw = {
            "summary": {"caption": "Scene", "canvas": "single"},
            "bbox_coord_space": "abs_centralized",
            "bbox_format": "yxyx",
            "items": [{"label": "text", "caption": "Title", "text": "HELLO\nMOSS", "bbox": [-400, -500, -200, 500]}],
        }
        result = moss.layout_from_metadata({"Prompt": json.dumps(raw), "Size": "1000x1000"})
        self.assertEqual(result["layout"]["items"][0]["bbox"], [100, 0, 300, 1000])
        self.assertEqual(result["layout"]["items"][0]["text"], "HELLO\nMOSS")
        self.assertIsNone(moss.layout_from_metadata({"Prompt": "ordinary image"}))

    def test_safety_includes_layout_and_literal_text(self):
        recipe = moss.moss_options([{"label": "text", "caption": "Sign", "text": "BLOCKED", "bbox": [0, 0, 100, 100]}])
        self.assertIn("BLOCKED", moss.safety_prompt({"prompt": "Safe scene", "moss": recipe}))
        self.assertIn("Sign", moss.safety_prompt({"prompt": "Safe scene", "moss": recipe}))

    def test_unsupported_legacy_features_have_clear_errors(self):
        for patch in (
            {"image": object()},
            {"regions": object()},
            {"vae": "sdxl.vae"},
            {"subseed_strength": 0.2},
            {"variation": 1},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                moss.build_moss_payload(params(**patch), moss.MOSS_SFT, False, [])

    def test_model_matching_is_exact(self):
        self.assertTrue(moss.is_moss(moss.MOSS_BASE.removesuffix(".safetensors")))
        self.assertFalse(moss.is_moss("random_moss_sdxl.safetensors"))


class PayloadIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        user = SimpleNamespace(checkpoint=AsyncMock(return_value="anima.safetensors"))
        self.config = SimpleNamespace(
            user=lambda _: user,
            checkpoint=AsyncMock(return_value="sdxl.safetensors"),
            vae=AsyncMock(return_value="old-vae.safetensors"),
            negative_prompt=AsyncMock(return_value="old global negative"),
            sampling_steps=AsyncMock(return_value=40),
            cfg=AsyncMock(return_value=7),
            sampler=AsyncMock(return_value="euler_ancestral"),
            scheduler=AsyncMock(return_value="beta"),
            width=AsyncMock(return_value=832),
            height=AsyncMock(return_value=1216),
            adetailer=AsyncMock(return_value=True),
        )
        self.cog = SimpleNamespace(config=self.config)
        self.build = builder()

    async def test_moss_ignores_anima_and_sdxl_globals(self):
        for checkpoint in moss.MOSS_MODELS:
            payload = await self.build(self.cog, params(checkpoint=checkpoint), object(), False)
            self.assertEqual(
                (payload["steps"], payload["cfg"], payload["samplerName"], payload["scheduler"]),
                (28, 4, "euler", "moss"),
            )
            self.assertEqual(payload["moss"], {"shift": 5})
            self.assertEqual(payload["negativePrompt"], "")
            self.assertEqual((payload["width"], payload["height"]), (1024, 1024))
            self.assertTrue(payload["sfwMode"])
            self.assertFalse(
                set(payload) & {"vaeName", "adetailer", "extraSeed", "extraSeedStrength", "attentionCouple"}
            )

    async def test_legacy_request_preserves_config_and_features(self):
        payload = await self.build(self.cog, params(negative_prompt="extra"), object(), True)
        self.assertEqual(payload["modelName"], "anima.safetensors")
        self.assertEqual(payload["negativePrompt"], "old global negative, extra")
        self.assertEqual((payload["steps"], payload["cfg"], payload["scheduler"]), (40, 7, "beta"))
        self.assertEqual(payload["vaeName"], "old-vae.safetensors")
        self.assertTrue(payload["adetailer"]["enabled"])
        self.assertFalse(payload["sfwMode"])
        self.assertNotIn("moss", payload)

    async def test_boxes_explicit_values_and_loras_reach_api(self):
        recipe = moss.moss_options(
            [{"label": "text", "caption": "Title", "text": "HELLO\nMOSS", "bbox": [20, 20, 200, 980]}]
        )
        payload = await self.build(
            self.cog,
            params(
                checkpoint=moss.MOSS_BASE,
                seed=42,
                steps=24,
                cfg=4.5,
                width=1216,
                height=832,
                moss=recipe,
                loras=["<lora:known-moss:0.6>"],
            ),
            object(),
            True,
        )
        self.assertEqual(payload["moss"], recipe)
        self.assertEqual(payload["loras"], [{"name": "known-moss.safetensors", "weight": "0.6"}])
        self.assertEqual((payload["seed"], payload["steps"], payload["cfg"]), (42, 24, 4.5))


class NativeMetadata(unittest.TestCase):
    def test_native_layout_json_is_preserved_and_not_mistaken_for_negative_text(self):
        import sys
        from io import BytesIO

        from PIL import Image, PngImagePlugin

        spec = importlib.util.spec_from_file_location("moss_comfy_reader", ROOT / "arcenciel/comfy.py")
        reader = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = reader
        spec.loader.exec_module(reader)
        layout = {
            "summary": {"canvas": "single", "caption": "A poster without watermark"},
            "bbox_coord_space": "abs_centralized",
            "bbox_format": "yxyx",
            "items": [{"label": "text", "caption": "Title", "text": "HELLO\nMOSS", "bbox": [-400, -500, -200, 500]}],
        }
        graph = {
            "11": {"class_type": "ArcEnCielMossTextEncode", "inputs": {"text": json.dumps(layout)}},
            "12": {"class_type": "ArcEnCielMossTextEncode", "inputs": {"text": "low quality"}},
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": moss.MOSS_SFT}},
            "4": {
                "class_type": "KSampler",
                "inputs": {"steps": 28, "cfg": 4, "seed": 42, "sampler_name": "euler", "scheduler": "moss"},
            },
        }
        info = PngImagePlugin.PngInfo()
        info.add_text("prompt", json.dumps(graph))
        data = BytesIO()
        Image.new("RGB", (1000, 1000)).save(data, format="PNG", pnginfo=info)
        result = reader.ComfyMetadataReader.from_bytes(data.getvalue()).as_dict()
        self.assertEqual(json.loads(result["Prompt"]), layout)
        self.assertEqual(result["Negative Prompt"], "low quality")
        self.assertEqual((result["Checkpoint"], result["Steps"], result["CFG"]), (moss.MOSS_SFT, 28, 4))
        self.assertEqual(moss.layout_from_metadata(result)["layout"]["items"][0]["text"], "HELLO\nMOSS")


if __name__ == "__main__":
    unittest.main()
