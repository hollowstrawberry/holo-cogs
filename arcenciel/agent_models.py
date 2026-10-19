"""Model and LoRA selection for conversational Arc en Ciel generation."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import NamedTuple

from .moss import is_array, is_object, is_strings, resolve_profile
from .moss_types import ImageParams, MossLora

FAMILIES = ("moss", "anima", "illustrious", "noobai")


class ModelChoice(NamedTuple):
    name: str
    family: str
    base: str


def _token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower()) if isinstance(value, str) else ""


def _base(value: object) -> str:
    token = _token(value)
    if token.startswith("noobai"):
        return "noobai"
    if token.startswith("illustrious"):
        return "illustrious"
    if token.startswith("anima"):
        return "anima"
    if token in ("mossimagesft", "mosssft", "mossimage"):
        return "moss_sft"
    if token in ("mossbase", "mossimagebase"):
        return "moss_base"
    return token


def _family(base: str) -> str:
    return "illustrious" if base in ("illustrious", "noobai") else "moss" if base.startswith("moss") else base


def _model_choice(entry: Mapping[str, object]) -> ModelChoice | None:
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        return None
    capabilities = entry.get("modelCapabilities")
    if capabilities is not None and not is_strings(capabilities):
        raise ValueError("Invalid generator model capabilities.")
    caps = capabilities or []
    base = (
        "moss_sft"
        if "moss_sft" in caps
        else "moss_base"
        if "moss" in caps
        else "anima"
        if "anima" in caps
        else _base(entry.get("baseModel"))
    )
    family = _family(base)
    if family not in ("moss", "anima", "illustrious"):
        return None
    return ModelChoice(name, family, base)


def available_models(catalog: Mapping[str, object]) -> dict[str, ModelChoice]:
    entries = catalog.get("checkpoints")
    if not is_array(entries):
        raise ValueError("Invalid generator model catalog.")
    found: dict[str, ModelChoice] = {}
    for entry in entries:
        if not is_object(entry):
            raise ValueError("Invalid generator model entry.")
        if entry.get("storageState") != "loaded":
            continue
        choice = _model_choice(entry)
        if choice:
            found[choice.name] = choice
    return found


def _matches(choice: ModelChoice, requested: str) -> bool:
    return requested == choice.family or requested == "noobai" and choice.base == "noobai"


def _name_matches(name: str, query: str) -> bool:
    return name.casefold() == query.casefold() or name.casefold().removesuffix(
        ".safetensors"
    ) == query.casefold().removesuffix(".safetensors")


def choose_model(
    options: Mapping[str, object],
    catalog: Mapping[str, object],
    *,
    family: str = "",
    checkpoint: str = "",
    inherited: str = "",
    default: str = "moss",
) -> ModelChoice:
    """Resolve only accessible loaded models; never infer a checkpoint from prompt text."""
    family = family.strip().lower()
    if family and family not in FAMILIES:
        raise ValueError("Choose moss, anima, illustrious or noobai as the model family.")
    models = available_models(catalog)
    selected = checkpoint.strip() or ("" if family else inherited.strip()) or (family or default.strip() or "moss")
    requested_family = family or (selected if selected in FAMILIES else "")
    if selected in FAMILIES:
        candidates = [choice for choice in models.values() if _matches(choice, selected)]
        if selected == "moss":
            name, _ = resolve_profile(options, catalog)
            return models[name]
        if selected == "illustrious":
            same_base = [choice for choice in candidates if choice.base == "illustrious"]
            if same_base:
                candidates = same_base
        if selected == "anima":
            limits = options.get("limits")
            anima = limits.get("anima") if is_object(limits) else None
            defaults = anima.get("defaults") if is_object(anima) else None
            configured = defaults.get("modelName") if is_object(defaults) else None
            if isinstance(configured, str):
                preferred_anima = [choice for choice in candidates if choice.name == configured]
                if preferred_anima:
                    return preferred_anima[0]
        configured_default = [choice for choice in candidates if _name_matches(choice.name, default.strip())]
        if len(configured_default) == 1:
            return configured_default[0]
        settings = options.get("settings")
        defaults = settings.get("defaults") if is_object(settings) else None
        server_default = defaults.get("modelName") if is_object(defaults) else None
        if isinstance(server_default, str):
            preferred = [choice for choice in candidates if choice.name == server_default]
            if preferred:
                return preferred[0]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise ValueError(f"No available {selected} checkpoint was found for this account.")
        raise ValueError(
            f"Several {selected} checkpoints are available. Configure an exact default or name one: "
            + ", ".join(choice.name for choice in candidates[:4])
        )
    candidates = [choice for choice in models.values() if _name_matches(choice.name, selected)]
    if not candidates:
        entries = catalog.get("checkpoints", [])
        if not is_array(entries):
            raise ValueError("Invalid generator model catalog.")
        for entry in entries:
            if not is_object(entry):
                continue
            entry_name, title = entry.get("name"), entry.get("modelTitle")
            if (
                isinstance(entry_name, str)
                and entry_name in models
                and isinstance(title, str)
                and title.casefold() == selected.casefold()
            ):
                candidates.append(models[entry_name])
    if not candidates and len(selected) >= 3:
        candidates = [choice for choice in models.values() if selected.casefold() in choice.name.casefold()]
    if not candidates:
        raise ValueError(f'Checkpoint "{selected}" is not available to this account.')
    if len(candidates) != 1:
        raise ValueError(f'Checkpoint "{selected}" is ambiguous.')
    choice = candidates[0]
    if requested_family and not _matches(choice, requested_family):
        raise ValueError(f'Checkpoint "{choice.name}" is not a {requested_family} model.')
    if choice.family == "moss":
        resolve_profile(options, catalog, choice.name)
    return choice


def choose_lora(entries: object, query: str, checkpoint: ModelChoice) -> str:
    if not is_array(entries):
        raise ValueError("Invalid LoRA search response.")
    compatible: list[tuple[str, str, str]] = []
    for entry in entries:
        if not is_object(entry):
            raise ValueError("Invalid LoRA catalog entry.")
        name, title = entry.get("name"), entry.get("modelTitle")
        base = _base(entry.get("baseModel"))
        if not isinstance(name, str):
            continue
        if not base:
            continue  # Unknown metadata is not evidence of compatibility.
        if _family(base) != checkpoint.family:
            continue
        if checkpoint.family == "moss" and base != checkpoint.base:
            continue
        compatible.append((name, base, title if isinstance(title, str) else ""))
    exact = [item for item in compatible if _name_matches(item[0], query) or item[2].casefold() == query.casefold()]
    pool = exact or compatible
    same_base = [item for item in pool if item[1] == checkpoint.base]
    if len(pool) > 1 and len(same_base) == 1:
        pool = same_base
    if len(pool) == 1:
        return pool[0][0]
    if not pool:
        raise ValueError(f'No compatible LoRA found for "{query}" and {checkpoint.name}.')
    raise ValueError(f'LoRA "{query}" is ambiguous. Name an exact file: ' + ", ".join(item[0] for item in pool[:4]))


def lora_family_hint(entries: object, query: str) -> str:
    """Infer a family only when an exact or sole catalog result is unambiguous."""
    if not is_array(entries):
        raise ValueError("Invalid LoRA search response.")
    candidates = [entry for entry in entries if is_object(entry) and isinstance(entry.get("name"), str)]
    exact = [
        entry
        for entry in candidates
        if _name_matches(str(entry["name"]), query) or str(entry.get("modelTitle") or "").casefold() == query.casefold()
    ]
    pool = exact or candidates
    if len(pool) != 1:
        return ""
    base = _base(pool[0].get("baseModel"))
    if base == "noobai":
        return "noobai"
    if base == "illustrious":
        return "illustrious"
    if base == "anima":
        return "anima"
    if base.startswith("moss"):
        return "moss"
    return ""


def lora_weight(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 2
    ):
        raise ValueError("LoRA weight must be between 0 and 2.")
    return float(value)


def anima_defaults(options: Mapping[str, object]) -> Mapping[str, object]:
    limits = options.get("limits")
    profile = limits.get("anima") if is_object(limits) else None
    defaults = profile.get("defaults") if is_object(profile) else None
    if not is_object(defaults) or not all(
        defaults.get(key) is not None for key in ("steps", "cfg", "samplerName", "scheduler", "width", "height")
    ):
        raise ValueError("The generator does not advertise Anima defaults. No image was queued.")
    return defaults


def build_standard_agent_payload(
    params: ImageParams, options: Mapping[str, object], choice: ModelChoice, nsfw: bool, loras: list[MossLora]
) -> dict[str, object]:
    if choice.family == "moss":
        raise ValueError("MOSS must use its own generation profile.")
    if params.image or params.regions or params.variation or params.subseed_strength or params.moss:
        raise ValueError("This model does not support MOSS boxes or legacy image variation in the agent tool.")
    payload: dict[str, object] = {
        "mode": "txt2img",
        "prompt": params.prompt,
        "modelName": choice.name,
        "seed": params.seed,
        "batchSize": 1,
        "loras": loras,
        "sfwMode": not nsfw,
    }
    if params.negative_prompt is not None:
        payload["negativePrompt"] = params.negative_prompt
    if choice.family == "anima":
        defaults = anima_defaults(options)
        payload.update({key: defaults[key] for key in ("steps", "cfg", "samplerName", "scheduler", "width", "height")})
    if params.width is not None:
        payload["width"] = params.width
    if params.height is not None:
        payload["height"] = params.height
    return payload
