# Agent image models on testing2026

The conversational Arc en Ciel tool now accepts MOSS, Anima and the
Illustrious/NoobAI family. GPT image generation and editing remain separate.
Illustrious and NoobAI LoRAs work with either checkpoint base; an exact base match
is preferred when several catalog results match a LoRA name.

## Configure the conversational default

`[p]agent image_default` shows the current per-guild default. The owner can set
`[p]agent image_default moss`, `anima`, `illustrious`, `noobai`, or an exact
accessible checkpoint filename. `[p]agent image_default reset` restores MOSS.
When a family has several available checkpoints and no matching generator
default, set an exact filename. An explicit model in the request overrides this
default; a referenced bot image otherwise supplies its original checkpoint.

The existing modular prompt editor accepts any key name. For example, create
`imagegen` and include `{imagegen}` in the responder prompt (and the
autoresponder prompt if it should generate images). No separate image prompt
setting is needed. Suggested contents, editable by the creator:

```text
Use the Arc en Ciel image tool for image requests. Its internal function name is
generate_stable_diffusion. Set checkpoint to moss, anima, illustrious or noobai
when the user requests a family, or to a specific available filename when they
request a model; otherwise let the tool use a referenced image, compatible LoRA
or configured default. Search for requested LoRAs and put their returned
<lora:filename:weight> tags in prompt. Never invent a filename. Describe the complete
scene and respect the requested composition. Only MOSS supports boxes for
placement and exact lettering. Coordinates are [top, left, bottom, right] in
0..1000. Do not put layout JSON or legacy || separators in the prompt. A
referenced bot image reuses its seed, size, checkpoint and LoRAs unless
overridden; it is rendered again, not edited at the pixel level. Wait for the
generated result before describing it. Explain model and LoRA errors rather
than silently changing the requested model.
```

The tool resolves available model names through `/generator/models`, searches
requested LoRAs through `/generator/models/loras`, and calls
`/generator/preflight` before queueing. Anima's defaults come from
`/generator/options` after the matching API change is deployed. Existing
channel restrictions, quota, SFW mode, filtering and spoiler handling apply.
The existing internal function name also prevents the legacy text fallback
from starting a duplicate job. Installing this PR does not rewrite guild
prompts, defaults or the running bot configuration.

## Previous MOSS rollout notes

The initial MOSS rollout added MOSS to the `arcenciel_image` agent tool and its
legacy text fallback.
`/moss` is the explicit command. Existing `/txt2img`, `/regional` and other model
commands keep their existing settings. This change targets `testing2026` and keeps
its previews, image search and spoiler handling. `agent/agent.py` is unchanged.

## Creator activation

Review and install the PR before activating it. There is no automatic prompt or
configuration migration. For each guild that uses the image tool:

1. Open the existing `[p]prompt` editor and create a custom key with the owner's
   preferred name, such as `imagegen`. `[p]` means the bot's configured command
   prefix. The instructions below are a starting point to edit, not a prompt
   injected by the code. No separate image prompt selection is required.
2. Add `{imagegen}` (or the chosen key name) to the existing responder prompt
   using `[p]prompt responder`, and to `[p]prompt autoresponder` if automatic
   responses should generate images. Remove conflicting Anima,
   booru-only and `||` image-generation instructions from those prompts. Create the
   key **before** referencing it: the built-in formatter rejects missing keys.
3. Check `[p]prompt full` and the autoresponder editor. The key is expanded by the
   built-in prompt system before the LLM selects tool arguments. Reading a key
   inside a tool invocation cannot retroactively guide that invocation.
4. Review `[p]agent tool setting`. The optional `checkpoint`, `sampler`,
   `scheduler`, `steps`, `cfg` and `shift` settings are explicit agent overrides.
   Clear an old override by sending `[p]agent tool setting checkpoint` (likewise
   for each other key). Empty values use the current server profile. These
   existing tool settings are global, while prompt keys are per guild.
5. Keep `arcenciel_image` enabled only where wanted. Existing generation-channel
   restrictions, moderator exceptions, server enablement, quotas, SFW mode and
   prompt filtering apply. The owner controls image instructions through the
   existing modular prompts; image generation has no separate prompt-key gate.
6. Test a normal scene, a left/right scene, exact lettering, reroll and modify in
   the intended Discord channel. Check the normal and automatic responders before
   broad use. The PR tests do not send Discord messages or alter live bot config.

Suggested editable prompt key for MOSS:

```text
Use the Arc en Ciel image tool for image requests. Its internal function name is
still generate_stable_diffusion, but it generates with MOSS. Describe the complete
scene in natural language: subjects, their relationships, setting, style and
important details. Prioritize the user's requested composition. Use optional
boxes only when subject placement or exact lettering matters. Coordinates are
[top, left, bottom, right] in 0..1000; each box must have positive width and height.
For a text box, put the exact lettering and line breaks in text, and its visual
appearance in caption. Keep the scene prompt readable: do not put layout JSON or
legacy || separators in it. Use only known compatible LoRAs. An existing bot image
is rendered again from its seed, size and layout, not edited at the pixel level.
Wait for the generated result before claiming what it depicts. If the tool
reports an unavailable profile or an invalid setting, explain the error instead
of trying a different model family.
```

Custom-key contents are inserted literally by the existing formatter; the tool
schema is shared and is never mutated with a guild's private prompt.

## API-driven recipes

Each newly prepared recipe fetches `/generator/options` and `/generator/models`
using the existing API credential. Model selection uses `modelCapabilities` and
`storageState`, not filenames or name substrings. With no explicit checkpoint,
exactly one available `moss_sft` model must exist. `/moss` autocomplete shows SFT
models and reuses the discovered catalog; every new recipe refreshes it. A creator can explicitly select another available MOSS checkpoint; an
ambiguous, missing or non-MOSS checkpoint fails with an actionable error.

Steps, CFG, shift, sampler, scheduler, dimensions and the maximum number of boxes
come from `limits.moss`. Explicit arguments take precedence over agent overrides,
which take precedence over server defaults. Ordinary per-user checkpoints and
legacy Arc en Ciel defaults (including negative prompts and ADetailer) do not leak
into new MOSS recipes. No substitute profile is used on discovery failure.
The generator API remains authoritative for supported combinations and limits.

Only stable wire-format facts are encoded locally: layout version, coordinate
order, normalized range and field validation. Left/right/title geometry in
`/moss` is a composition preset, not a model default. The tool has no fixed box
count in its JSON schema; the current API limit validates every request.

Reroll/Modify reuse the saved recipe so a change to server defaults does not change
an existing image's settings. MOSS layouts and literal text survive these actions;
unsupported variation/upscale actions are hidden. A named existing bot image can
supply its seed, dimensions and native MOSS layout for a new rendering, using the
current profile. Passing `boxes: []` clears that recovered layout. ImageScanner is
an optional fallback after native PNG metadata parsing.

The internal function name `generate_stable_diffusion` is retained because the
existing agent uses it to prevent a tool result from triggering a second image
through its text fallback. Both entry points share permission/profile handling
via the agent request marker, without adding a second LLM image option or editing
`agent/agent.py`.

The API session advertises `gzip, deflate`, the decoders available in the bot's
installed runtime, including for preview events.

## Type boundaries

MOSS helpers and the agent tool annotate parameters and return values. Validated
model profiles, boxes, options and outgoing payloads use the `TypedDict` records
in `moss_types.py`. Untrusted JSON and tool input remain `object` until runtime
checks establish their shape; malformed data fails before an image is queued.
Optional cog lookups are narrowed before accessing cog-specific methods.

The scoped strict check below covers the MOSS types, helpers and agent tool with
the installed bot dependencies. Imported legacy modules are resolved but their
existing diagnostics are outside this check; it is not a repository-wide clean
typecheck. Run it from the repository root, without suppressing missing imports.

```bash
PYTHONPATH=.:/path/to/installed/cogs:/path/to/Downloader/lib \
  /path/to/bot/python -m mypy --config-file mypy-moss.ini
```

Verification used mypy 2.0.0 in an isolated checker directory. This is a development
tool, not a new bot runtime dependency.

## Focused verification

```bash
python3.11 -m pytest tests/test_moss.py tests/test_generator_previews.py tests/test_arcenciel_images.py
# In an isolated candidate directory with the installed Redbot dependencies:
PYTHONPATH=.:/path/to/installed/cogs:/path/to/Downloader/lib \
  /path/to/bot/python -B -m unittest discover -s tests -p 'test_moss*.py' -v
```

The runtime suite imports the real cogs and executes their tool, text fallback,
commands and Discord views with mocked transport/configuration. Live API checks
should separately cover discovery with the bot credential, preflight and actual
image generation with a temporary test-user credential. Do not install or reload
the candidate in the running bot as part of PR verification.
