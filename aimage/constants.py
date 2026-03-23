import re

ENDPOINT = "https://arcenciel.io/api"

VIEW_TIMEOUT = 15 * 60

LORA_REGEX = re.compile(r"(<lora:([^:]+):(\d+\.?\d*)>)")

UUID_PREFIX_REGEX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[-_ ]+", re.IGNORECASE)
NUMERIC_PREFIX_REGEX = re.compile(r"^(?:\d{3,}[_-]){2,}")
LORA_PREFIX_REGEX = re.compile(r'^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9]+(?:_[0-9]+)?)_',re.IGNORECASE)

ADETAILER_ARGS = {
  "adetailer": {
        "enabled": True,
        "model": "yolov8n.pt",
        "detector": "yolov8n.pt",
        "confidence": 0.3,
        "iou": 0.45,
        "dilate": 4,
        "maskBlur": 4,
        "maxDetections": 8,
        "detectionOrder": "left-to-right"
    }
}

PARAMS_BLACKLIST = [
    "Template",
    "ADetailer confidence", "ADetailer mask", "ADetailer dilate", "ADetailer denoising", "ADetailer steps",
    "ADetailer inpaint", "ADetailer version", "ADetailer prompt", "ADetailer use", "ADetailer checkpoint",
]

DEFAULT_TAGGER = "wd-vit-large-tagger-v3"

DEFAULT_THRESHOLD = 0.2

EXCLUDE_TAGGER = ["general", "sensitive", "questionable", "explicit"]
