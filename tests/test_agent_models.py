"""Pure selection tests; no bot login or Discord transport."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

root = Path(__file__).resolve().parents[1] / "arcenciel"
package = types.ModuleType("agent_models_contract")
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location("agent_models_contract.agent_models", root / "agent_models.py")
models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(models)


def profile():
    options = {
        "limits": {
            "moss": {
                "available": True,
                "modes": ["txt2img"],
                "maxBoxes": 8,
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
            "anima": {
                "defaults": {
                    "modelName": "anima.safetensors",
                    "steps": 30,
                    "cfg": 4,
                    "samplerName": "euler_ancestral",
                    "scheduler": "beta",
                    "width": 1024,
                    "height": 1024,
                }
            },
        },
        "settings": {"defaults": {"modelName": "illus.safetensors"}},
    }
    catalog = {
        "checkpoints": [
            {
                "name": "moss.safetensors",
                "baseModel": "MOSS-Image SFT",
                "modelCapabilities": ["moss", "moss_sft"],
                "storageState": "loaded",
            },
            {
                "name": "anima.safetensors",
                "baseModel": "Anima",
                "modelCapabilities": ["anima"],
                "storageState": "loaded",
            },
            {
                "name": "illus.safetensors",
                "baseModel": "Illustrious 1.0",
                "modelCapabilities": ["sdxl"],
                "storageState": "loaded",
            },
            {
                "name": "noob.safetensors",
                "baseModel": "NoobAI Eps",
                "modelCapabilities": ["sdxl"],
                "storageState": "loaded",
            },
            {
                "name": "hidden.safetensors",
                "baseModel": "NoobAI Eps",
                "modelCapabilities": ["sdxl"],
                "storageState": "not_loaded",
            },
        ]
    }
    return options, catalog


class AgentModelSelection(unittest.TestCase):
    def test_lora_catalog_response_requires_object_entries(self):
        self.assertEqual(
            models.parse_lora_entries({"entries": [{"name": "one.safetensors"}]}),
            [{"name": "one.safetensors"}],
        )
        with self.assertRaisesRegex(ValueError, "Invalid generator LoRA catalog"):
            models.parse_lora_entries({"entries": "not a list"})
        with self.assertRaisesRegex(ValueError, "Invalid generator LoRA catalog entry"):
            models.parse_lora_entries({"entries": [None]})

    def test_configured_default_explicit_switch_and_inherited_recipe(self):
        options, catalog = profile()
        self.assertEqual(models.choose_model(options, catalog).name, "moss.safetensors")
        self.assertEqual(models.choose_model(options, catalog, default="anima").name, "anima.safetensors")
        self.assertEqual(models.choose_model(options, catalog, default="noob.safetensors").base, "noobai")
        self.assertEqual(models.choose_model(options, catalog, family="illustrious").name, "illus.safetensors")
        options["settings"]["defaults"]["modelName"] = "noob.safetensors"
        self.assertEqual(models.choose_model(options, catalog, family="illustrious").name, "illus.safetensors")
        self.assertEqual(models.choose_model(options, catalog, family="noobai").name, "noob.safetensors")
        options["settings"]["defaults"]["modelName"] = "noob.safetensors"
        self.assertEqual(
            models.choose_model(options, catalog, family="illustrious", default="illus.safetensors").name,
            "illus.safetensors",
        )
        self.assertEqual(models.choose_model(options, catalog, inherited="noob.safetensors").name, "noob.safetensors")
        self.assertEqual(
            models.choose_model(options, catalog, inherited="noob.safetensors", family="anima").name,
            "anima.safetensors",
        )
        with self.assertRaisesRegex(ValueError, "not a noobai"):
            models.choose_model(options, catalog, family="noobai", checkpoint="illus.safetensors")
        with self.assertRaisesRegex(ValueError, "not available"):
            models.choose_model(options, catalog, checkpoint="hidden.safetensors")

    def test_ambiguous_family_requires_an_exact_default(self):
        options, catalog = profile()
        options["settings"]["defaults"]["modelName"] = "moss.safetensors"
        catalog["checkpoints"].append({
            "name": "illus-other.safetensors",
            "baseModel": "Illustrious 1.0",
            "modelCapabilities": ["sdxl"],
            "storageState": "loaded",
        })
        with self.assertRaisesRegex(ValueError, "Several illustrious checkpoints"):
            models.choose_model(options, catalog, default="illustrious")

    def test_lora_prefers_exact_base_but_accepts_cross_base(self):
        options, catalog = profile()
        checkpoint = models.choose_model(options, catalog, family="noobai")
        entries = [
            {"name": "illus-lora.safetensors", "baseModel": "Illustrious 1.0", "modelTitle": "Character"},
            {"name": "noob-lora.safetensors", "baseModel": "NoobAI Eps", "modelTitle": "Character"},
        ]
        self.assertEqual(models.choose_lora(entries, "Character", checkpoint), "noob-lora.safetensors")
        self.assertEqual(models.choose_lora(entries, "illus-lora.safetensors", checkpoint), "illus-lora.safetensors")
        with self.assertRaisesRegex(ValueError, "No compatible LoRA"):
            models.choose_lora([{"name": "pony.safetensors", "baseModel": "Pony"}], "pony", checkpoint)
        with self.assertRaisesRegex(ValueError, "No compatible LoRA"):
            models.choose_lora([{"name": "unknown.safetensors", "baseModel": None}], "unknown", checkpoint)
        self.assertEqual(models.lora_family_hint([entries[0]], "illus-lora.safetensors"), "illustrious")

    def test_standard_payload_uses_anima_profile_and_omits_other_family_defaults(self):
        options, catalog = profile()
        params = NS(
            prompt="one person",
            negative_prompt=None,
            width=None,
            height=None,
            seed=-1,
            image=None,
            regions=None,
            variation=0,
            subseed_strength=0,
            moss=None,
        )
        anima = models.build_standard_agent_payload(
            params, options, models.choose_model(options, catalog, family="anima"), False, []
        )
        self.assertEqual((anima["modelName"], anima["steps"], anima["cfg"]), ("anima.safetensors", 30, 4))
        self.assertNotIn("negativePrompt", anima)
        illustrious = models.build_standard_agent_payload(
            params, options, models.choose_model(options, catalog, family="illustrious"), True, []
        )
        self.assertFalse(illustrious["sfwMode"])
        self.assertNotIn("steps", illustrious)
        options["limits"].pop("anima")
        with self.assertRaisesRegex(ValueError, "does not advertise Anima defaults"):
            models.build_standard_agent_payload(
                params, options, models.choose_model(options, catalog, family="anima"), False, []
            )


if __name__ == "__main__":
    unittest.main()
