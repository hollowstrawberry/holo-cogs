import re

ENDPOINT = "https://arcenciel.io/api"

VIEW_TIMEOUT = 15 * 60
JOB_TIMEOUT = 10 * 60
PROGRESS_UPDATE_INTERVAL = 5
MAX_UPLOAD_PIXELS = 2048*2048
MAX_MESSAGE_LENGTH = 2000

SUPPORTED_IMAGE_TYPES = ["png", "jpg", "jpeg"]

LORA_PATTERN = re.compile(r"(<lora:([^:]+):(\d+\.?\d*)>)")
RESOURCE_FILE_PATTERN = re.compile(r"\"[^\"]+\.(?:safetensors|ckpt|pth|pt|bin)\"", re.IGNORECASE)
RESOURCE_HASH_PATTERN = re.compile(r"\b(?:0x)?[0-9a-f]{10,64}\b", re.IGNORECASE)

UUID_PREFIX_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[-_ ]+", re.IGNORECASE)
NUMERIC_PREFIX_PATTERN = re.compile(r"^(?:\d{3,}[_-]){2,}")
LORA_PREFIX_PATTERN = re.compile(r'^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9]+(?:_[0-9]+)?)_',re.IGNORECASE)

NEWLINE_SEPARATOR_PATTERN = re.compile(r",? *\n[\n\s]*")
PIPE_SEPARATOR_PATTERN = re.compile(r"\s*\|\|\s*")

DEFAULT_UPSCALER = "2x-AnimeSharpV4_RCAN.safetensors"
DEFAULT_DENOISE = 20
DEFAULT_ADETAILER_DENOISE = 50

ADETAILER_ARGS = {
    "enabled": True,
    "model": "478_1062_Anzhc_20Face_20seg_201024_20v2_20y8n.pt",
    "detector": "478_1062_Anzhc_20Face_20seg_201024_20v2_20y8n.pt",
    "confidence": 0.3,
    "denoise": DEFAULT_ADETAILER_DENOISE,
    "iou": 0.5,
    "dilate": 4,
    "maskBlur": 4,
    "maxDetections": 2,
    "detectionOrder": "left-to-right",
    "maskMode": "segmentation",
    "timing": "post-upscale",
}

PARAMS_BLACKLIST = [
    "Template",
    "ADetailer confidence", "ADetailer mask", "ADetailer dilate", "ADetailer denoising", "ADetailer steps",
    "ADetailer inpaint", "ADetailer version", "ADetailer prompt", "ADetailer use", "ADetailer checkpoint",
]

EXCLUDE_TAGGER = ["general", "sensitive", "questionable", "explicit"]
