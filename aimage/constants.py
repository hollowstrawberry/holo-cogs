import re

ENDPOINT = "https://arcenciel.io/api"

VIEW_TIMEOUT = 15 * 60
JOB_TIMEOUT = 10 * 60
PROGRESS_UPDATE_INTERVAL = 5

SUPPORTED_IMAGE_TYPES = ["png", "jpg", "jpeg"]

LORA_REGEX = re.compile(r"(<lora:([^:]+):(\d+\.?\d*)>)")

UUID_PREFIX_REGEX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[-_ ]+", re.IGNORECASE)
NUMERIC_PREFIX_REGEX = re.compile(r"^(?:\d{3,}[_-]){2,}")
LORA_PREFIX_REGEX = re.compile(r'^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9]+(?:_[0-9]+)?)_',re.IGNORECASE)

ADETAILER_ARGS = {
  "adetailer": {
        "enabled": True,
        "model": "478_1062_Anzhc_20Face_20seg_201024_20v2_20y8n.pt",
        "detector": "478_1062_Anzhc_20Face_20seg_201024_20v2_20y8n.pt",
        "confidence": 0.3,
        "denoise": 0.45,
        "iou": 0.5,
        "dilate": 4,
        "maskBlur": 4,
        "maxDetections": 4,
        "detectionOrder": "left-to-right",
        "timing": "post-upscale",
    }
}

PARAMS_BLACKLIST = [
    "Template",
    "ADetailer confidence", "ADetailer mask", "ADetailer dilate", "ADetailer denoising", "ADetailer steps",
    "ADetailer inpaint", "ADetailer version", "ADetailer prompt", "ADetailer use", "ADetailer checkpoint",
]

EXCLUDE_TAGGER = ["general", "sensitive", "questionable", "explicit"]
