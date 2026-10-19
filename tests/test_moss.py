"""Dependency-light MOSS contract tests, runnable with unittest on the bot runtime."""

import importlib.util
import json
import sys
import types
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

package = types.ModuleType("moss_contract")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "arcenciel")]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(
    "moss_contract.moss", Path(__file__).resolve().parents[1] / "arcenciel/moss.py"
)
moss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(moss)


def server_profile() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "limits": {
                "moss": {
                    "available": True,
                    "modes": ["txt2img"],
                    "maxBoxes": 3,
                    "coordinateSpace": "normalized_1000",
                    "defaults": {
                        "steps": 26,
                        "cfg": 3.5,
                        "shift": 6,
                        "samplerName": "euler",
                        "scheduler": "moss",
                        "width": 768,
                        "height": 1024,
                    },
                },
                "samplers": ["euler", "heun"],
                "schedulers": ["moss"],
                "anima": {
                    "defaults": {
                        "modelName": "anima.safetensors",
                        "steps": 30, "cfg": 4, "samplerName": "euler_ancestral",
                        "scheduler": "beta", "width": 1024, "height": 1024,
                    }
                },
            }
        },
        {
            "checkpoints": [
                {
                    "name": "renamed-sft.safetensors",
                    "modelCapabilities": ["moss", "moss_sft"],
                    "storageState": "loaded",
                },
                {
                    "name": "research-base.safetensors",
                    "modelCapabilities": ["moss", "moss_base"],
                    "storageState": "loaded",
                },
                {"name": "anima.safetensors", "modelCapabilities": ["anima"], "storageState": "loaded"},
                {"name": "illus.safetensors", "baseModel": "Illustrious 1.0", "modelCapabilities": [], "storageState": "loaded"},
                {"name": "noob.safetensors", "baseModel": "NoobAI Eps", "modelCapabilities": [], "storageState": "loaded"},
            ]
        },
    )


def params(**kwargs: object) -> NS:
    values = dict(
        prompt="A painted seaside village.",
        negative_prompt=None,
        width=None,
        height=None,
        steps=None,
        cfg=None,
        sampler=None,
        scheduler=None,
        checkpoint=None,
        seed=-1,
        moss={},
        image=None,
        regions=None,
        subseed_strength=0,
        variation=0,
        vae=None,
    )
    values.update(kwargs)
    return NS(**values)


def box() -> dict[str, Any]:
    return dict(label="character", caption="A woman in red", bbox=[10, 10, 900, 480])


class MossContract(unittest.TestCase):
    def test_selected_image_prompt_key_is_required(self) -> None:
        keys = {"imagegen": "Creator instructions", "moss": " "}
        self.assertEqual(moss.configured_image_prompt(keys, "imagegen"), "Creator instructions")
        with self.assertRaisesRegex(ValueError, "`moss`"):
            moss.configured_image_prompt(keys, "moss")
        with self.assertRaisesRegex(ValueError, "`missing`"):
            moss.configured_image_prompt(keys, "missing")
        with self.assertRaisesRegex(ValueError, "select an image prompt key"):
            moss.configured_image_prompt(keys, "")

    def test_discovery_follows_changed_models_and_server_defaults(self) -> None:
        options, catalog = server_profile()
        for name, steps, cfg, shift, width in [
            ("renamed-sft.safetensors", 26, 3.5, 6, 768),
            ("future-model.bin", 32, 5, 8, 1152),
        ]:
            catalog["checkpoints"][0]["name"] = name
            options["limits"]["moss"]["defaults"].update(steps=steps, cfg=cfg, shift=shift, width=width)
            result = moss.build_moss_payload(params(), options, catalog, False, [])
            self.assertEqual(
                (result["modelName"], result["steps"], result["cfg"], result["moss"]["shift"], result["width"]),
                (name, steps, cfg, shift, width),
            )
            self.assertTrue(result["sfwMode"])
            self.assertEqual(result["negativePrompt"], "")
            self.assertFalse({"vaeName", "attentionCouple", "adetailer", "extraSeedStrength"} & result.keys())

    def test_no_silent_model_or_profile_fallback(self) -> None:
        options, catalog = server_profile()
        for checkpoint in ["anima.safetensors", "removed.safetensors"]:
            with self.subTest(checkpoint=checkpoint), self.assertRaisesRegex(ValueError, "not an available MOSS"):
                moss.build_moss_payload(params(checkpoint=checkpoint), options, catalog, False, [])
        for field, value in [("available", False), ("defaults", {}), ("coordinateSpace", "future"), ("modes", [])]:
            bad = deepcopy(options)
            bad["limits"]["moss"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                moss.resolve_profile(bad, catalog)
        catalog["checkpoints"].append({**catalog["checkpoints"][0], "name": "other.safetensors"})
        with self.assertRaisesRegex(ValueError, "unique MOSS SFT"):
            moss.resolve_profile(options, catalog)
        self.assertEqual(moss.resolve_profile(options, catalog, "research-base")[0], "research-base.safetensors")

    def test_overrides_and_validation(self) -> None:
        options, catalog = server_profile()
        result = moss.build_moss_payload(
            params(cfg=4.5), options, catalog, True, [], {"steps": "29", "cfg": "5.5", "shift": "7", "sampler": "heun"}
        )
        self.assertEqual(
            (result["steps"], result["cfg"], result["samplerName"], result["moss"]["shift"]), (29, 4.5, "heun", 7)
        )
        self.assertFalse(result["sfwMode"])
        for setting in [
            {"steps": "abc"},
            {"steps": "24.5"},
            {"steps": 24.5},
            {"cfg": "nan"},
            {"sampler": "removed"},
            {"shift": "nan"},
            {"shift": "bad"},
            {"shift": True},
            {"cfg": []},
            {"steps": float("inf")},
            {"sampler": ["euler"]},
        ]:
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                moss.build_moss_payload(params(), options, catalog, False, [], setting)

    def test_malformed_discovery_is_rejected_before_payload_construction(self) -> None:
        options, catalog = server_profile()
        for invalid in ([], {"defaults": []}, {"defaults": {"steps": True}}):
            bad = deepcopy(options)
            bad["limits"]["moss"] = invalid
            with self.subTest(profile=invalid), self.assertRaises(ValueError):
                moss.build_moss_payload(params(), bad, catalog, False, [])
        for checkpoints in ({}, [None], [{"modelCapabilities": "moss_sft"}]):
            with self.subTest(checkpoints=checkpoints), self.assertRaises(ValueError):
                moss.build_moss_payload(params(), options, {"checkpoints": checkpoints}, False, [])

    def test_dynamic_box_limit_and_geometry(self) -> None:
        for count in [1, 2, 4]:
            self.assertEqual(len(moss.moss_options([box()] * count, max_boxes=count)["layout"]["items"]), count)
            with self.assertRaisesRegex(ValueError, str(count)):
                moss.moss_options([box()] * (count + 1), max_boxes=count)
        for coords in [[0, 0, 0, 1000], [0, 500, 1000, 100], [-1, 0, 10, 10], [False, 0, 10, 10], [0, 0, 1.5, 2]]:
            bad = box()
            bad["bbox"] = coords
            with self.subTest(coords=coords), self.assertRaises(ValueError):
                moss.moss_options([bad], max_boxes=3)
        for invalid in [None, "boxes", {}, [{"label": "text", "caption": "Title", "bbox": [0, 0, 100, 100]}]]:
            recipe = {
                "layout": {"version": 1, "coordinateSpace": "normalized_1000", "canvas": "single", "items": invalid}
            }
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                moss.validate_options(recipe, max_boxes=3)

    def test_metadata_preserves_geometry_and_multiline_text(self) -> None:
        raw = {
            "summary": {"caption": "Poster"},
            "items": [{"label": "text", "caption": "Title", "text": "HELLO\nMOSS", "bbox": [-512, -512, -256, 512]}],
            "bbox_coord_space": "abs_centralized",
            "bbox_format": "yxyx",
        }
        recipe = moss.layout_from_metadata({"Prompt": json.dumps(raw), "Size": "1024x1024"})
        self.assertEqual(recipe["layout"]["items"][0]["bbox"], [0, 0, 250, 1000])
        self.assertIn("HELLO\nMOSS", moss.safety_prompt({"prompt": "Scene", "moss": recipe}))
        self.assertIsNone(moss.layout_from_metadata({"Prompt": "ordinary prompt"}))

    def test_unsupported_legacy_features_rejected(self) -> None:
        options, catalog = server_profile()
        for field in ["image", "regions", "variation", "subseed_strength", "vae"]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                moss.build_moss_payload(params(**{field: 1}), options, catalog, False, [])


if __name__ == "__main__":
    unittest.main()
