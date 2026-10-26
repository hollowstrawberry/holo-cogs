import re
import math
import json
from typing import TypeGuard
from collections.abc import Mapping
from redbot.core import commands

from arcenciel import types


def is_object(value: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(value, Mapping) and all(isinstance(key, str) for key in value)


def is_array(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def is_strings(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def is_box_label(value: object) -> TypeGuard[types.BoxLabel]:
    return isinstance(value, str) and value in ("character", "object", "text")


def json_object(value: object) -> Mapping[str, object]:
    if not is_object(value):
        raise ValueError("Expected a JSON object from the generator.")
    return value


def positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"Invalid MOSS {field} setting.")
    try:
        result = int(value)
        if result <= 0 or float(value) != result:
            raise ValueError()
    except (ValueError, OverflowError) as error:
        raise ValueError(f"Invalid MOSS {field} setting.") from error
    return result


def positive_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"Invalid MOSS {field} setting.")
    try:
        result = float(value)
        if not math.isfinite(result) or result <= 0:
            raise ValueError()
    except (ValueError, OverflowError) as error:
        raise ValueError(f"Invalid MOSS {field} setting.") from error
    return result


def string_setting(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid MOSS {field} setting.")
    return value


def moss_models(catalog: Mapping[str, object], *, sft_only: bool = False) -> dict[str, types.MossModel]:
    required = "moss_sft" if sft_only else "moss"
    entries = catalog.get("checkpoints", [])
    if not is_array(entries):
        raise ValueError("Invalid generator model catalog.")
    models: dict[str, types.MossModel] = {}
    for entry in entries:
        if not is_object(entry):
            raise ValueError("Invalid generator model entry.")
        capabilities = entry.get("modelCapabilities")
        if capabilities is None:
            continue
        if not is_strings(capabilities):
            raise ValueError("Invalid generator model capabilities.")
        if required not in capabilities or entry.get("storageState") != "loaded":
            continue
        name = string_setting(entry.get("name"), "checkpoint")
        models[name] = {"name": name, "modelCapabilities": list(capabilities), "storageState": "loaded"}
    return models


def resolve_profile(
    options: Mapping[str, object], catalog: Mapping[str, object], checkpoint: str | None = None
) -> tuple[str, types.MossProfile]:
    limits = json_object(options.get("limits", {}))
    profile = json_object(limits.get("moss") or {})
    modes = profile.get("modes", [])
    if not profile.get("available") or not is_strings(modes) or "txt2img" not in modes:
        raise ValueError("MOSS is unavailable on the generator. No image was queued.")
    max_boxes = profile.get("maxBoxes")
    if profile.get("coordinateSpace") != "normalized_1000" or type(max_boxes) is not int:
        raise ValueError("The generator advertises an unsupported MOSS layout contract.")
    raw_defaults = json_object(profile.get("defaults") or {})
    required = ("steps", "cfg", "shift", "samplerName", "scheduler", "width", "height")
    if any(raw_defaults.get(key) is None for key in required):
        raise ValueError("The generator's MOSS defaults are incomplete.")
    defaults: types.MossDefaults = {
        "steps": positive_int(raw_defaults["steps"], "steps"),
        "cfg": positive_float(raw_defaults["cfg"], "cfg"),
        "shift": positive_float(raw_defaults["shift"], "shift"),
        "samplerName": string_setting(raw_defaults["samplerName"], "sampler"),
        "scheduler": string_setting(raw_defaults["scheduler"], "scheduler"),
        "width": positive_int(raw_defaults["width"], "width"),
        "height": positive_int(raw_defaults["height"], "height"),
    }
    models = moss_models(catalog)
    if checkpoint:
        # Match the API name, accepting the extension-less form used by existing settings.
        matches = [name for name in models if name == checkpoint or name.removesuffix(".safetensors") == checkpoint]
        if len(matches) != 1:
            raise ValueError(
                "The configured checkpoint is not an available MOSS model. Clear or update the checkpoint setting."
            )
        checkpoint = matches[0]
    else:
        candidates = moss_models(catalog, sft_only=True)
        if len(candidates) != 1:
            raise ValueError("No unique MOSS SFT model is available. Select an available MOSS checkpoint explicitly.")
        checkpoint = next(iter(candidates))
    return checkpoint, {
        "available": True,
        "modes": list(modes),
        "coordinateSpace": "normalized_1000",
        "maxBoxes": max_boxes,
        "defaults": defaults,
    }


def moss_options(boxes: object = None, shift: object = None, *, max_boxes: int) -> types.MossOptions:
    result: types.MossOptions = {}
    if shift is not None:
        if (
            isinstance(shift, bool)
            or not isinstance(shift, (int, float))
            or not math.isfinite(shift)
            or not 0.1 <= shift <= 20
        ):
            raise ValueError("MOSS shift must be between 0.1 and 20.")
        result["shift"] = float(shift)
    if boxes is None or boxes == []:
        return result
    if not is_array(boxes) or len(boxes) > max_boxes:
        raise ValueError(f"Provide at most {max_boxes} regional boxes.")
    clean: list[types.MossBox] = []
    for box in boxes:
        if not is_object(box) or set(box) - {"label", "caption", "text", "bbox"}:
            raise ValueError("Each box needs label, caption and bbox, with text only for a text box.")
        label, caption, bbox = box.get("label"), box.get("caption"), box.get("bbox")
        if not is_box_label(label) or not isinstance(caption, str) or not caption.strip() or len(caption) > 2048:
            raise ValueError("Every box needs a type and a description of up to 2048 characters.")
        if not is_array(bbox) or len(bbox) != 4:
            raise ValueError("Box coordinates must be four integers in 0..1000: top, left, bottom, right.")
        coordinates: list[int] = []
        for coordinate in bbox:
            if type(coordinate) is not int or not 0 <= coordinate <= 1000:
                raise ValueError("Box coordinates must be four integers in 0..1000: top, left, bottom, right.")
            coordinates.append(coordinate)
        if coordinates[0] >= coordinates[2] or coordinates[1] >= coordinates[3]:
            raise ValueError("Every box must have positive width and height.")
        item: types.MossBox = {"label": label, "caption": caption, "bbox": coordinates}
        if label == "text":
            text = box.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 1024:
                raise ValueError("Text boxes need exact text of up to 1024 characters.")
            item["text"] = text
        elif "text" in box:
            raise ValueError("Only text boxes can contain exact text.")
        clean.append(item)
    result["layout"] = {"version": 1, "coordinateSpace": "normalized_1000", "canvas": "single", "items": clean}
    return result


def validate_options(options: object, *, max_boxes: int) -> types.MossOptions:
    if not is_object(options) or set(options) - {"shift", "layout"}:
        raise ValueError("Invalid MOSS options.")
    layout = options.get("layout")
    if layout is not None and not is_object(layout):
        raise ValueError("Invalid MOSS layout format.")
    result = moss_options(None if layout is None else layout.get("items"), options.get("shift"), max_boxes=max_boxes)
    if layout and "layout" in result:
        if (
            set(layout) - {"version", "coordinateSpace", "canvas", "items", "meta"}
            or layout.get("version") != 1
            or layout.get("coordinateSpace") != "normalized_1000"
            or layout.get("canvas") != "single"
            or not layout.get("items")
        ):
            raise ValueError("Invalid MOSS layout format.")
        if "meta" in layout:
            meta = layout["meta"]
            if not is_strings(meta) or len(meta) > 8 or any(not tag.strip() or len(tag) > 80 for tag in meta):
                raise ValueError("Layout style needs up to eight short descriptions.")
            result["layout"]["meta"] = list(meta)
    return result


def setting(value: object, overrides: Mapping[str, object], name: str, default: object) -> object:
    return overrides.get(name, default) if value is None or value == "" else value


def build_moss_payload(
    params: types.ImageGenParams,
    options: Mapping[str, object],
    catalog: Mapping[str, object],
    nsfw: bool,
    loras: list[types.MossLora],
    overrides: Mapping[str, object] | None = None,
) -> types.MossPayload:
    settings = {key: value for key, value in (overrides or {}).items() if value not in (None, "")}
    selected = params.checkpoint or settings.get("checkpoint")
    checkpoint, profile = resolve_profile(
        options, catalog, None if selected is None else string_setting(selected, "checkpoint")
    )
    if params.image or params.regions or params.subseed_strength or params.variation:
        raise ValueError(
            "MOSS supports text-to-image and Regional Boxes; "
            "image editing, regional masks and variations are unavailable."
        )
    if params.vae:
        raise ValueError("MOSS uses its locked VAE. Leave the VAE option empty.")
    if not params.prompt.strip():
        raise ValueError("Provide a scene description.")
    defaults = profile["defaults"]
    sampler = string_setting(setting(params.sampler, settings, "sampler", defaults["samplerName"]), "sampler")
    scheduler = string_setting(setting(params.scheduler, settings, "scheduler", defaults["scheduler"]), "scheduler")
    limits = json_object(options.get("limits", {}))
    for value, field, collection in ((sampler, "samplerName", "samplers"), (scheduler, "scheduler", "schedulers")):
        choices = limits.get(collection, [])
        if not is_strings(choices) or value not in choices:
            raise ValueError(f"The selected MOSS {field} is not available on the generator.")
    recipe: dict[str, object] = dict(params.moss or {})
    if "shift" not in recipe:
        recipe["shift"] = positive_float(settings.get("shift", defaults["shift"]), "shift")
    return {
        "mode": "txt2img",
        "prompt": params.prompt,
        "negativePrompt": params.negative_prompt or "",
        "modelName": checkpoint,
        "seed": params.seed,
        "batchSize": 1,
        "loras": loras,
        "sfwMode": not nsfw,
        "steps": positive_int(setting(params.steps, settings, "steps", defaults["steps"]), "steps"),
        "cfg": positive_float(setting(params.cfg, settings, "cfg", defaults["cfg"]), "cfg"),
        "width": positive_int(setting(params.width, settings, "width", defaults["width"]), "width"),
        "height": positive_int(setting(params.height, settings, "height", defaults["height"]), "height"),
        "samplerName": sampler,
        "scheduler": scheduler,
        "moss": validate_options(recipe, max_boxes=profile["maxBoxes"]),
    }


def layout_from_metadata(metadata: Mapping[str, object]) -> types.MossOptions | None:
    """Recover only the documented native layout; never execute workflow data."""
    try:
        prompt, size = metadata.get("Prompt", ""), metadata.get("Size", "")
        if not isinstance(prompt, str) or not isinstance(size, str):
            return None
        raw: object
        raw, end = json.JSONDecoder().raw_decode(prompt.lstrip())
        suffix = prompt.lstrip()[end:].strip()
        if suffix and not re.fullmatch(r"(?:<lora:[^<>]+>\s*)+", suffix):
            return None
        if not is_object(raw) or raw.get("bbox_coord_space") != "abs_centralized" or raw.get("bbox_format") != "yxyx":
            return None
        width, height = map(int, size.split("x"))
        if width <= 0 or height <= 0:
            return None
        items = raw.get("items")
        if not is_array(items):
            return None
        boxes: list[dict[str, object]] = []
        for item in items:
            if not is_object(item):
                return None
            bbox = item.get("bbox")
            if not is_array(bbox) or len(bbox) != 4:
                return None
            coordinates: list[int] = []
            for value, dimension in zip(bbox, (height, width, height, width), strict=True):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return None
                coordinates.append(max(0, min(1000, round((value / dimension + 0.5) * 1000))))
            box = {k: item[k] for k in ("label", "caption", "text") if k in item}
            box["bbox"] = coordinates
            boxes.append(box)
        result = moss_options(boxes, max_boxes=len(boxes))
        if "meta" in raw and "layout" in result:
            meta = raw["meta"]
            if not is_strings(meta):
                return None
            result["layout"]["meta"] = list(meta)
        return validate_options(result, max_boxes=len(boxes))
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def safety_prompt(payload: Mapping[str, object]) -> str:
    parts: list[str] = []
    prompt = payload.get("prompt", "")
    if isinstance(prompt, str):
        parts.append(prompt)
    moss = payload.get("moss")
    layout = moss.get("layout") if is_object(moss) else None
    if is_object(layout):
        items = layout.get("items", [])
        if is_array(items):
            for box in items:
                if is_object(box):
                    for key in ("caption", "text"):
                        value = box.get(key)
                        if isinstance(value, str):
                            parts.append(value)
        meta = layout.get("meta")
        if is_strings(meta):
            parts.extend(meta)
    return "\n".join(parts)
