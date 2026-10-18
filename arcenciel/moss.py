"""MOSS API contract helpers. Models, defaults and box limits come from the API."""

import json
import math
import re
from copy import deepcopy


def moss_models(catalog, *, sft_only=False):
    required = "moss_sft" if sft_only else "moss"
    return {
        entry["name"]: entry
        for entry in catalog.get("checkpoints", [])
        if required in (entry.get("modelCapabilities") or []) and entry.get("storageState") == "loaded"
    }


def resolve_profile(options, catalog, checkpoint=None):
    profile = options.get("limits", {}).get("moss") or {}
    if not profile.get("available") or "txt2img" not in profile.get("modes", []):
        raise ValueError("MOSS is unavailable on the generator. No image was queued.")
    if profile.get("coordinateSpace") != "normalized_1000" or type(profile.get("maxBoxes")) is not int:
        raise ValueError("The generator advertises an unsupported MOSS layout contract.")
    defaults = profile.get("defaults") or {}
    required = ("steps", "cfg", "shift", "samplerName", "scheduler", "width", "height")
    if any(defaults.get(key) is None for key in required):
        raise ValueError("The generator's MOSS defaults are incomplete.")
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
    return checkpoint, profile


def moss_options(boxes=None, shift=None, *, max_boxes):
    if shift is not None and (
        isinstance(shift, bool)
        or not isinstance(shift, (int, float))
        or not math.isfinite(shift)
        or not 0.1 <= shift <= 20
    ):
        raise ValueError("MOSS shift must be between 0.1 and 20.")
    result = {} if shift is None else {"shift": shift}
    if boxes is None or boxes == []:
        return result
    if not isinstance(boxes, list) or len(boxes) > max_boxes:
        raise ValueError(f"Provide at most {max_boxes} regional boxes.")
    clean = []
    for box in boxes:
        if not isinstance(box, dict) or set(box) - {"label", "caption", "text", "bbox"}:
            raise ValueError("Each box needs label, caption and bbox, with text only for a text box.")
        label, caption, bbox = box.get("label"), box.get("caption"), box.get("bbox")
        if (
            label not in ("character", "object", "text")
            or not isinstance(caption, str)
            or not caption.strip()
            or len(caption) > 2048
        ):
            raise ValueError("Every box needs a type and a description of up to 2048 characters.")
        if not isinstance(bbox, list) or len(bbox) != 4 or any(type(n) is not int or not 0 <= n <= 1000 for n in bbox):
            raise ValueError("Box coordinates must be four integers in 0..1000: top, left, bottom, right.")
        if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            raise ValueError("Every box must have positive width and height.")
        if label == "text":
            text = box.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 1024:
                raise ValueError("Text boxes need exact text of up to 1024 characters.")
        elif "text" in box:
            raise ValueError("Only text boxes can contain exact text.")
        clean.append(deepcopy(box))
    result["layout"] = {"version": 1, "coordinateSpace": "normalized_1000", "canvas": "single", "items": clean}
    return result


def validate_options(options, *, max_boxes):
    if not isinstance(options, dict) or set(options) - {"shift", "layout"}:
        raise ValueError("Invalid MOSS options.")
    layout = options.get("layout")
    result = moss_options(
        None if layout is None else layout.get("items") if isinstance(layout, dict) else "invalid",
        options.get("shift"),
        max_boxes=max_boxes,
    )
    if layout is not None:
        if (
            not isinstance(layout, dict)
            or set(layout) - {"version", "coordinateSpace", "canvas", "items", "meta"}
            or layout.get("version") != 1
            or layout.get("coordinateSpace") != "normalized_1000"
            or layout.get("canvas") != "single"
            or not layout.get("items")
        ):
            raise ValueError("Invalid MOSS layout format.")
        if "meta" in layout:
            meta = layout["meta"]
            if (
                not isinstance(meta, list)
                or len(meta) > 8
                or any(not isinstance(tag, str) or not tag.strip() or len(tag) > 80 for tag in meta)
            ):
                raise ValueError("Layout style needs up to eight short descriptions.")
            result["layout"]["meta"] = list(meta)
    return result


def build_moss_payload(params, options, catalog, nsfw, loras, overrides=None):
    overrides = {key: value for key, value in (overrides or {}).items() if value not in (None, "")}
    checkpoint, profile = resolve_profile(options, catalog, params.checkpoint or overrides.get("checkpoint"))
    if params.image or params.regions or params.subseed_strength or params.variation:
        raise ValueError(
            "MOSS supports text-to-image and Regional Boxes; "
            "image editing, regional masks and variations are unavailable."
        )
    if params.vae:
        raise ValueError("MOSS uses its locked VAE. Leave the VAE option empty.")
    if not isinstance(params.prompt, str) or not params.prompt.strip():
        raise ValueError("Provide a scene description.")
    defaults = profile["defaults"]
    payload = {
        "mode": "txt2img",
        "prompt": params.prompt,
        "negativePrompt": params.negative_prompt or "",
        "modelName": checkpoint,
        "seed": params.seed,
        "batchSize": 1,
        "loras": loras,
        "sfwMode": not nsfw,
    }
    for field, attribute, converter in (
        ("steps", "steps", int),
        ("cfg", "cfg", float),
        ("samplerName", "sampler", str),
        ("scheduler", "scheduler", str),
        ("width", "width", int),
        ("height", "height", int),
    ):
        value = getattr(params, attribute)
        if value is None or value == "":
            value = overrides.get(attribute, defaults[field])
        if isinstance(value, bool):
            raise ValueError(f"Invalid MOSS {attribute} setting.")
        try:
            converted = converter(value)
            if converter in (int, float) and (
                not math.isfinite(converted) or converted <= 0 or converter is int and float(value) != converted
            ):
                raise ValueError()
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError(f"Invalid MOSS {attribute} setting.") from error
        payload[field] = converted
    for field, collection in (("samplerName", "samplers"), ("scheduler", "schedulers")):
        if payload[field] not in options.get("limits", {}).get(collection, []):
            raise ValueError(f"The selected MOSS {field} is not available on the generator.")
    recipe = deepcopy(getattr(params, "moss", None) or {})
    if "shift" not in recipe:
        try:
            recipe["shift"] = float(overrides.get("shift", defaults["shift"]))
        except (ValueError, TypeError) as error:
            raise ValueError("Invalid MOSS shift setting.") from error
    payload["moss"] = validate_options(recipe, max_boxes=profile["maxBoxes"])
    return payload


def agent_settings(agent, context):
    """Use existing guild prompt keys and permissions for tools AND the text fallback."""
    if agent is None:
        raise ValueError("The agent cog is unavailable.")
    config = agent.config[context.guild]
    if "arcenciel_image" not in config.enabled_functions.value:
        raise ValueError("The image tool is not enabled for this server.")
    prompt = (config.prompt_keys.value or {}).get("moss")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(
            "The bot owner must configure the moss prompt key and include {moss} "
            "in the responder prompts before using MOSS."
        )
    mode, channels = config.generation_channel_mode.value, config.generation_channels.value
    if (
        mode == "blacklist"
        and context.channel.id in channels
        or mode == "whitelist"
        and context.channel.id not in channels
    ) and not context.channel.permissions_for(context.author).manage_messages:
        raise ValueError("Image generation is not allowed in this channel unless the user is a moderator.")
    return agent.config.tool_settings.value or {}


def layout_from_metadata(metadata):
    """Recover only the documented native layout; never execute workflow data."""
    try:
        prompt = metadata.get("Prompt", "")
        raw, end = json.JSONDecoder().raw_decode(prompt.lstrip())
        suffix = prompt.lstrip()[end:].strip()
        if suffix and not re.fullmatch(r"(?:<lora:[^<>]+>\s*)+", suffix):
            return None
        if raw.get("bbox_coord_space") != "abs_centralized" or raw.get("bbox_format") != "yxyx":
            return None
        width, height = map(int, metadata.get("Size", "").split("x"))
        if width <= 0 or height <= 0:
            return None
        boxes = []
        for item in raw["items"]:
            box = {k: item[k] for k in ("label", "caption", "text") if k in item}
            top, left, bottom, right = item["bbox"]
            box["bbox"] = [
                max(0, min(1000, round((value / size + 0.5) * 1000)))
                for value, size in ((top, height), (left, width), (bottom, height), (right, width))
            ]
            boxes.append(box)
        result = moss_options(boxes, max_boxes=len(boxes))
        if isinstance(raw.get("meta"), list):
            result["layout"]["meta"] = raw["meta"]
        return result
    except (ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError):
        return None


def safety_prompt(payload):
    parts = [payload.get("prompt", "")]
    for box in payload.get("moss", {}).get("layout", {}).get("items", []):
        parts.extend([box.get("caption", ""), box.get("text", "")])
    parts.extend(payload.get("moss", {}).get("layout", {}).get("meta", []))
    return "\n".join(parts)
