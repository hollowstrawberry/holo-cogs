# MOSS on testing2026

The `arcenciel_image` agent tool and the agent's legacy text fallback now use MOSS.
`/moss` is the explicit command. Existing `/txt2img`, `/regional` and other model
commands keep their existing settings. This change targets `testing2026` and keeps
its previews, image search and spoiler handling. `agent/agent.py` is unchanged.

## Creator activation

Review and install the PR before activating it. There is no automatic prompt or
configuration migration. For each guild that uses the image tool:

1. Open the existing `[p]prompt` editor and create a custom key named `moss`.
   `[p]` means the bot's configured command prefix. The instructions below are a
   starting point to edit, not a prompt injected by the code.
2. Add `{moss}` to the existing responder and autoresponder prompts using
   `[p]prompt responder` and `[p]prompt autoresponder`. Remove conflicting Anima,
   booru-only and `||` image-generation instructions from those prompts. Create the
   key **before** referencing it: the built-in formatter rejects missing keys.
3. Check `[p]prompt full` and the autoresponder editor. The key is expanded by the
   built-in prompt system before the LLM selects tool arguments. Reading a key
   inside a tool invocation cannot retroactively guide that invocation.
4. Review `[p]agent tool setting`. The optional `checkpoint`, `sampler`,
   `scheduler`, `steps`, `cfg` and `shift` settings are explicit agent overrides.
   Clear an old override by sending `[p]agent tool setting checkpoint` (likewise
   for each other key). Empty values use the current server profile. These
   existing tool settings are global, while the prompt key is per guild.
5. Keep `arcenciel_image` enabled only where wanted. Existing generation-channel
   restrictions, moderator exceptions, server enablement, quotas, SFW mode and
   prompt filtering apply. A missing/empty `moss` key blocks agent generation,
   including the old text fallback; explicit `/moss` does not require an LLM key.
6. Test a normal scene, a left/right scene, exact lettering, reroll and modify in
   the intended Discord channel. Check the normal and automatic responders before
   broad use. The PR tests do not send Discord messages or alter live bot config.

Suggested editable `moss` key:

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
