"""MOSS request recipes, shared by explicit commands and the agent tool.

Coordinates are [top, left, bottom, right] in 0..1000. The API owns model
loading, safety checks and compilation to MOSS's centered pixel coordinates.
"""

import json
import math
import re
from copy import deepcopy

MOSS_SFT = "moss_image_sft_int8_convrot.safetensors"
MOSS_BASE = "moss_image_base_int8_convrot.safetensors"
MOSS_MODELS = (MOSS_SFT, MOSS_BASE)


def is_moss(checkpoint):
    return str(checkpoint or "").removesuffix(".safetensors") + ".safetensors" in MOSS_MODELS


def moss_options(boxes=None, shift=5):
    if (
        isinstance(shift, bool)
        or not isinstance(shift, (int, float))
        or not math.isfinite(shift)
        or not 0.1 <= shift <= 20
    ):
        raise ValueError("MOSS shift must be between 0.1 and 20.")
    result = {"shift": shift}
    if boxes is None or boxes == []:
        return result
    if not isinstance(boxes, list) or len(boxes) > 8:
        raise ValueError("Provide at most eight regional boxes.")
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


def validate_options(options):
    if not isinstance(options, dict) or set(options) - {"shift", "layout"}:
        raise ValueError("Invalid MOSS options.")
    layout = options.get("layout")
    result = moss_options(
        None if layout is None else layout.get("items") if isinstance(layout, dict) else "invalid",
        options.get("shift", 5),
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


def build_moss_payload(params, checkpoint, nsfw, loras):
    if params.image or params.regions or params.subseed_strength or params.variation:
        raise ValueError(
            "MOSS currently supports text-to-image and Regional Boxes; "
            "image editing, old regional masks and variations are unavailable."
        )
    if params.vae:
        raise ValueError("MOSS uses its own Flux2 VAE. Leave the VAE option empty.")
    return {
        "mode": "txt2img",
        "prompt": params.prompt,
        "negativePrompt": params.negative_prompt or "",
        "modelName": checkpoint.removesuffix(".safetensors") + ".safetensors",
        "width": params.width or 1024,
        "height": params.height or 1024,
        "steps": params.steps or 28,
        "cfg": 4.0 if params.cfg is None else float(params.cfg),
        "samplerName": params.sampler or "euler",
        "scheduler": params.scheduler or "moss",
        "seed": params.seed,
        "batchSize": 1,
        "loras": loras,
        "sfwMode": not nsfw,
        "moss": validate_options(getattr(params, "moss", None) or {"shift": 5}),
    }


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
        result = moss_options(boxes)
        if isinstance(raw.get("meta"), list):
            result["layout"]["meta"] = raw["meta"]
        return validate_options(result)
    except (ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError):
        return None


def safety_prompt(payload):
    parts = [payload.get("prompt", "")]
    for box in payload.get("moss", {}).get("layout", {}).get("items", []):
        parts.extend([box.get("caption", ""), box.get("text", "")])
    parts.extend(payload.get("moss", {}).get("layout", {}).get("meta", []))
    return "\n".join(parts)
