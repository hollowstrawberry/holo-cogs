# Responder caching

`[p]agent cache_mode fixed_prefix` places one OpenRouter cache breakpoint at the
last text block of the initial user context. This is the default. The boundary stays in place while
tool replies are appended. `[p]agent cache_mode implicit` sends no breakpoints.
Native OpenAI requests always omit OpenRouter cache metadata.

The helper that creates text blocks no longer adds cache metadata to every block.
Auxiliary calls use implicit provider caching. The responder snapshots its model,
limits, prompt keys and cache policy once per response, sorts enabled tool schemas,
and preserves the SDK assistant message including reasoning signatures.

With extended logging enabled, each responder API call emits `model_call=` JSON:
request/generation IDs, configured model, reported provider, iteration, elapsed
milliseconds, prefix/schema/message fingerprints, tool choice, and unmodified
provider usage (including cache reads/writes and cost when supplied). These entries
contain no prompt text or image data. Existing extended logs remain unchanged.
The existing token and cost totals are not reinterpreted or corrected heuristically.

## Verification

Run focused regressions in a Red runtime with this repository and its external
cogs/dependencies on `PYTHONPATH`:

```sh
python -m unittest discover -s tests -p '*cache*.py' -v
```

`scripts/cache_probe.py` is a manual, paid experiment, never part of automatic
tests. It runs synthetic five-call tool chains through the installed OpenAI SDK.
It executes no Discord commands or real tools. Supply the Red core settings file
with `--credentials`; the OpenRouter key stays in process memory. Use one private
`--output` directory for the entire investigation, and run only one probe process
at a time. The shared ledger caps attempts at 120 and reserves USD 0.05 per request
within a USD 3 total ceiling. Failed requests retain their reservation. Provider
fallbacks are disabled and existing account/privacy restrictions still apply.

The comparison arms are legacy moving breakpoints, implicit Gemini, a fixed Gemini
prefix, and implicit DeepSeek. Model IDs and providers in this dated experiment are
fixtures, not production routing policy. `--mode`, `--no-fake` and `--keep-tools`
allow isolating the final tool-choice transition; `--image` and `--detail` exercise
mixed image/text input. The image fixture contains red, green and blue blocks.
Private wire artifacts contain synthetic data and must not be committed.

## Production experiment — 2026-10-24

Baseline: `hollowstrawberry/holo-cogs@f88a680`, Python 3.11.15, OpenAI SDK
1.109.1, discord.py 2.7.1, Red 3.5.24. Probes used the production container's
installed dependencies and account, synthetic context, and inert tool results.
No probe sent a Discord message or ran a real bot utility.

The primary comparison used three independent five-call chains per arm. Each
chain had four fixture tool calls and a final answer. Context, tool schema,
reasoning effort and output cap were held stable; each chain received a fresh
nonce. Gemini was restricted to Google Vertex. The account selected Wafer for
all completed DeepSeek reference chains; provider identity stayed constant within
each chain. Existing privacy exclusions were respected.

| Model / policy                             | Mean cost per five calls | Warm calls with cache reads | Warm input tokens read from cache |
| ------------------------------------------ | -----------------------: | --------------------------: | --------------------------------: |
| Gemini 3.7 Flash / previous moving markers |                $0.026271 |                        8/12 |                             30.2% |
| Gemini 3.7 Flash / implicit                |                $0.027550 |                        5/12 |                             19.0% |
| Gemini 3.7 Flash / fixed prefix            |                $0.011848 |                       12/12 |                             94.2% |
| DeepSeek V4.1 Flash 1017 / implicit, Wafer |                $0.001474 |                       12/12 |                             95.3% |

The fixed Gemini policy reduced mean chain cost by **54.9%** against the previous
implementation. Mean model request time per chain was 15.51 seconds versus 15.87
seconds previously, so this experiment does not demonstrate a material latency
improvement. Implicit Gemini had a 31-second outlier. These fixtures measure
cache behavior and billed cost; conversational quality was not scored.

### Findings and boundaries

- The first explicit Gemini cache request reported 16,123 input tokens, 8,062
  cache-write tokens and 8,062 cache-read tokens. Input spikes coincide with cache
  creation even though the wire prefix never shrinks. Cache-write accounting is
  not consistent enough to subtract writes from every input total. Preserve the
  raw usage and reconcile cost through generation metadata.
- A fixed prefix remained cached through subsequent tool results. Gemini created
  another cache when the final request switched to `tool_choice=none`. Removing
  the synthetic final tool exchange did not remove this write; retaining `auto`
  did. Production keeps `none` to enforce the existing tool limit.
- A separate DeepSeek fixed-prefix chain also completed successfully, with four
  warm cache hits and no explicit cache writes. Its mean-chain-equivalent cost
  was $0.001469. This supports compatibility, not guaranteed cache hits.
- The real `AgentCog.execute_responder` method completed full five-call chains
  against both Gemini and DeepSeek with the new policy. It preserved the context
  prefix and produced the expected final answer. DeepSeek had one final cache
  miss in this test. An installed-code implicit control later completed with
  warm hits; two preceding control attempts ended early with an empty answer and
  were excluded from the comparison. Provider caching and model output remain
  best effort; this change does not claim to fix every provider failure.
- Current Gemini Vertex honored `image_url.detail=low` in the fixture. The same
  1024×768 PNG produced initial cached prefixes of 9,137 tokens with `high`,
  9,139 with `auto`, and 8,339 with `low` (nonce tokenization varies slightly).
  Reducing the image to 384×288 at `low` produced 8,336. All four chains correctly
  identified the colored blocks. This is not an OCR/detail-quality evaluation;
  production image size/detail settings were left unchanged.

### Verification and rollout

- Eight focused regressions pass locally, in the staged production runtime, and
  against the installed production module. They cover image-first text extraction,
  prefix boundaries, native OpenAI requests, SDK reasoning metadata, settings
  changed mid-chain, tool depth and telemetry.
- Strict mypy passes for the new policy helper. Checking the existing modified
  modules reports 12 pre-existing diagnostics, versus 14 on the baseline, with no
  new diagnostics. Ruff passes on new Python files; Markdown was formatted with
  Prettier. No broad coverage run was needed.
- The investigation made **119 completion attempts** within the 120-call / $3
  budget. Reported successful-response cost was **$0.300638**. Three provider/routing
  failures retained a conservative $0.15 budget reservation. The first direct
  DeepSeek route was excluded by account privacy policy, DeepInfra returned an
  upstream 429, and the Nebius route returned 404. Incomplete pilot/control chains
  were excluded from the main table.
- Code and agent configuration were backed up before testing and again directly
  before deployment. Five Python files were installed under source-hash guards,
  the bot was restarted, and all deployed hashes matched the reviewed files.
  Discord reconnection and successful Agent loading were verified. The agent
  configuration file was byte-for-byte unchanged, and no startup errors appeared.
- Full synthetic wire records, generation metadata and rollback archives are
  retained privately. No credentials, production prompts or private chat history
  are included in this repository.
