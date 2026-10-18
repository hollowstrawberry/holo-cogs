# MOSS Image

The Arc en Ciel image tool now uses MOSS SFT INT8 by default. It is still enabled as
`arcenciel_image`, so existing enabled-tool lists need no change. Its LLM function
is `generate_moss_image`; the responder receives MOSS-specific scene and layout
instructions. The old text fallback uses that same tool and channel permissions.
No separate Anima image tool is added. Existing GPT image tools keep their own configuration.

`moss_checkpoint` can select either `moss_image_sft_int8_convrot.safetensors` or
`moss_image_base_int8_convrot.safetensors`. Existing `checkpoint`, `sampler` and
`scheduler` tool settings do not override the MOSS profile. If a custom responder
prompt explicitly names the old function or requires booru tags/Anima, update that
text to describe MOSS and natural-language scenes.

The default recipe is 28 steps, CFG 4, shift 5, Euler and the `moss` scheduler.
The API loads the Qwen3.5 encoder and FLUX.2 VAE. MOSS requests omit inherited
SDXL/Anima VAE, ADetailer and variation settings. Existing `/txt2img`,
`/txt2img-regions` and `/img2img` requests for other models retain their behavior.

Use `/moss` for an explicit command. It offers SFT/Base, aspect ratio, seed,
negative prompt, optional left/right subject descriptions and an exact title.
The command reuses `txt2img` permissions, VIP/quota and channel safety rules.
Regional Boxes also require the permissions configured by the Arc en Ciel API.

For the LLM, ordinary requests need only a natural-language scene prompt. Optional
`boxes` place up to eight characters, objects or text elements. Coordinates are
`[top, left, bottom, right]` in 0..1000; `top < bottom` and `left < right`. Text boxes require a visual description and literal `text`:

```json
{
  "prompt": "A bright travel poster with two friends at a mountain cafe.",
  "resolution": "landscape",
  "boxes": [
    { "label": "character", "caption": "Woman in a red jacket, waving", "bbox": [200, 20, 980, 480] },
    { "label": "character", "caption": "Man in a blue jacket holding coffee", "bbox": [200, 520, 980, 980] },
    { "label": "text", "caption": "Large clear heading", "text": "HELLO\nMOSS", "bbox": [10, 30, 180, 970] }
  ]
}
```

Box descriptions and literal text pass through the existing blacklist and API
safety checks. Invalid boxes fail before queueing. Layouts are retained by reroll
and prompt modification; the modify dialog shows the natural scene prompt.
An `existing` bot filename makes a fresh rendition using its seed, size and MOSS
layout. Supplying `boxes: []` clears the old layout. This is not pixel editing.

MOSS currently supports text-to-image. Variation, image-to-image, legacy region
masks and hires are not enabled; unsupported buttons are hidden for MOSS results.
The complete generated PNG workflow remains available in image information.

The API client advertises gzip/deflate compression to remain compatible with
Redbot installations whose optional Brotli binding cannot decode newer aiohttp responses.

## Verification

Pure recipe, legacy payload and PNG metadata tests (Pillow required):

```sh
python -m unittest discover -s tests -p test_moss.py -v
```

With the installed Redbot/Discord dependencies available, test the actual tool,
slash command, channel checks, result actions and modify dialog without logging
in or sending messages:

```sh
python -m unittest discover -s tests -p 'test_moss*.py' -v
```

Deploy both `agent` and `arcenciel` together after review, then synchronize slash
commands using the bot owner's usual procedure. The live bot is not modified by
these tests.
