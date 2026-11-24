# ComfyUI-LLMClient reference

LLM nodes for ComfyUI over any OpenAI-compatible API (llama.cpp, vLLM,
NInfer, Strata, OpenAI, ...): prompt generation and enhancement, image
captioning and analysis, text extraction — `output` routes to any string
socket in your graph.

See the [installation instructions](../README.md#install) for dependencies.

## Nodes

### LLM Provider

- `base_url` — OpenAI-compatible API root; `/v1` appended if missing.
- `model` — combo filled by **Test Connection**. The probe auto-detects the
  server profile (Strata / llama.cpp / vLLM / generic) and caches models, context
  size, and vision flags in `providers.json`; also filled on first execution.
  The cache is stored automatically in `llmclient/providers.json` under
  ComfyUI's user directory. An existing cache in the plugin directory is
  automatically copied on first use and left in place. Optionally, set
  `COMFYUI_LLMCLIENT_CACHE_PATH` before starting ComfyUI to use another
  directory instead; that directory has its own cache.
  The button label reports the result, e.g. `OK (3 models · llamacpp ·
  8192 ctx)`.
- Output: `model` handle for LLM Chat / LLM Agent / LLM Unload.

### LLM Chat

The reply is available as an output when generation finishes; it is not displayed live.

- `prompt`, optional `system_prompt`, optional `image`.
- `vision` — `auto` checks known capabilities, `enabled` forces image input,
  and `disable` rejects connected images.
- `model_config` — optional sampling overrides (see LLM Model Config).
- `reasoning_effort` — empty defaults to `medium`; provider-specific levels
  only (`low`, `medium`, `high`, `xhigh`, ...), invalid values are rejected
  by the server.
- `seed` — `-1` = random.
- Outputs: `output`, and `reasoning` (thinking trace when the model emits
  one).

### LLM Agent Config

Purpose and tools for the LLM Agent.

- `system_prompt` — the agent's job and rules. Empty = built-in generic
  agent instructions. Not tied to image gen: write any task (prompt
  authoring, summarization, research, code...).
- `output_format` — `text`, or `json` (final answer forced to a single JSON
  object; pair with LLM Pattern Extract). `json_hint` lists the required
  keys, e.g. `positive, negative, seed, steps, cfg, sampler, scheduler`.
- `assets_tool` — tools that see your ComfyUI install: `list_local_models`
  (checkpoints, diffusion models, LoRA, embeddings...) and
  `get_model_details` (embedded safetensors metadata: description cards,
  trigger words, training tags; plus sidecar `.txt`/`.md`/`.json` card
  files next to each model).
- `web_tool` — `web_search` and `web_fetch`: search the web, then read
  the full page of a promising result — model cards, trigger words, docs.
  `web_search` tries engines in order and fails over automatically:
  Tavily (if a key is configured), SearXNG (if a server URL is configured),
  then DuckDuckGo as a keyless last resort.
  Blocked engines cool down with exponential backoff, results are cached,
  and when every engine is unavailable the tool returns an actionable
  message to the model instead of failing the run. `web_fetch` only accesses
  public addresses and does not follow redirects. Local LLM and configured
  SearXNG servers are unaffected. See *Credentials*
  below for the Tavily key.
- `tool_mode` (advanced) — `auto` uses native OpenAI tool-calling and falls
  back to a JSON text protocol when the server rejects tools. `native`
  requires support (llama.cpp: `--jinja`; vLLM:
  `--enable-auto-tool-choice --tool-call-parser ...`). `json` uses the text
  protocol directly.
- `max_iterations` (advanced) — agent loop cap, default 18. When the
  budget runs out the agent gets one final tools-off turn to write its
  answer; a truncated answer is prefixed `[max iterations reached]`.

### LLM Agent

Agentic loop: model → tool calls → results → ... until a final answer.
The final answer is available when the run finishes; text is not displayed live.

- `model`, optional `agent_config` (unconnected = generic agent, all tools
  on), `prompt`, optional `model_config`, `image`, `reasoning_effort`, `seed`.
- `vision` — like LLM Chat, `auto` rejects images for models detected as
  text-only; `enabled` forces it, and `disable` rejects images. Only the first image in a batch is sent.
- Outputs: `output` (final answer), `reasoning`, and `session` — the full
  transcript (JSON) of every assistant turn, tool call and tool result.
  Wire `output`/`session` to any text viewer. When an image is connected,
  the session includes its encoded image data.

Example: ask *"write a positive/negative prompt pair for a night cityscape,
use local LoRAs that fit and include their trigger words"* with
`output_format=json` + `json_hint=positive, negative` → feed the output to
LLM Pattern Extract → CLIP Text Encode.

### LLM Pattern Extract

Fans one LLM output string out into labeled sockets — replaces chained
regex nodes.

- `text` — LLM Chat / LLM Agent `output`.
- `mode` — `json` (key path per line; empty = label, dots for nesting like
  `sampler.name`) or `regex` (named group `(?P<value>...)` or group 1;
  prefix `(?s)` for multiline).
- `patterns` — one `label = pattern` per line; sockets rename live from
  your labels (max 12). `#` lines are comments.
- Missing matches are empty strings. JSON output from LLM Chat/Agent works
  even when fenced in ```json blocks.

### LLM Model Config

Optional sampling and request overrides. `-1` = provider default for `temperature`,
`top_p`, `top_k`, `max_tokens`. `reasoning_effort` lives directly on LLM
Chat / LLM Agent instead. `timeout_seconds` defaults to 300 and limits each
complete model request, including streaming; increase it for slow models.
For the agent this applies separately to each model turn, not the entire run.
An unconnected config also uses 300 seconds.

### LLM Unload

`offload_vram_to_ram` / `release_all` / `wake_up`. Support depends on the
server (matrix below); unsupported servers fail with a clear error.

Connect LLM Chat's `output` to the optional `text` input, then use LLM
Unload's `text` output downstream to ensure generation finishes before
unloading and downstream processing waits for the unload command. Text is
passed through unchanged; `status` remains the first output. Leave `text`
unconnected for standalone commands such as `wake_up`.

## Unload support

| Server | What works | Requirements |
|--------|-----------|--------------|
| vLLM | sleep level 1 (weights to RAM), level 2 (discard), wake | `--enable-sleep-mode` and `VLLM_SERVER_DEV_MODE=1` |
| llama.cpp | `/models/load`, `/models/unload` | router mode (`--models-dir`) |
| Strata | `release_all` unloads; `wake_up` loads ahead of a request | A Strata version with `/unload` and `/load`; the next request also loads automatically. `offload_vram_to_ram` is not supported. |

### Free ComfyUI Models

Use this node after image or video generation to request unloading all
ComfyUI-managed models and clear unused device cache before starting an LLM.
Connect any data to `anything` and route the `anything` output to the next operation; the
value and its type are preserved. The `status` output is a string available
after cleanup. Connect an output to the LLM stage to enforce ordering.

`free_vram` unloads managed models from devices and clears unused device cache.
`free_vram_and_ram` additionally runs Python garbage collection
for unreferenced objects. It does not clear workflow caches or guarantee that
referenced model weights are released from RAM or reloaded from disk.

This does not unload external providers such as Strata, or guarantee release
of memory held outside ComfyUI's model manager. ComfyUI may reload models
when a later node needs them.

## Credentials

There is no API-key input on any node — keys can't end up in a workflow.
Open **Manage API Keys** on an LLM Provider node, or **LLM API Keys**
in the bottom panel. Store keys only for servers that require them; each
entry is a `base_url` + key pair kept in the configured credential store:
You can optionally give each saved provider a display name; matching still
uses the base URL. A server that requires no API key needs no entry here.

- Windows Credential Manager is used when available; otherwise a
  DPAPI-protected file under `%LOCALAPPDATA%\ComfyUI-LLMClient\` (macOS
  Keychain / Linux Secret Service on those platforms). If those backends
  are unavailable, a permissions-restricted plaintext file is used. The API key manager
  shows which store is active. API keys are not saved in browser settings:
  the input is cleared after saving and the list only shows whether an API
  key is saved.
- The key follows the URL: switching `base_url` (llama.cpp ↔ NInfer ↔
  Strata ...) automatically selects the stored credential for that
  server. Servers that do not require authentication need no entry at all.
- If you upgrade from a version that had an `api_key` widget: the widget
  is gone, but old workflow files still contain the literal key. Delete
  and re-add the provider node, re-save the workflow, and rotate any key
  you already shared.
- Credentials are resolved server-side at request time and are never
  placed in outputs, history, payloads or logs. Redirects on
  credentialed requests are refused so keys cannot leak to a different
  host. Plaintext HTTP with a stored key is allowed only for private /
  VPN literal addresses (loopback, `10/8`, `172.16/12`, `192.168/16`,
  `100.64/10` Tailscale-CGNAT, link-local, ULA); anything public —
  including hostnames — must use HTTPS.
- `web_search` uses the same API key manager: set the Tavily API key in its
  web search section (or env `TAVILY_API_KEY`). Configure a SearXNG server
  URL in the same section; the saved URL takes precedence over the optional
  `SEARXNG_URL` environment variable. Removing the saved URL falls back to
  the environment variable, if set. Enable `json` under `search.formats` in
  the SearXNG server's `settings.yml`.

**Warning**: the ComfyUI server itself has no authentication — anyone who
can reach it can add or delete vault entries (existing keys are only ever
read back masked). Do not store keys on a machine whose ComfyUI is
exposed to an untrusted network (`--listen 0.0.0.0`), and be cautious
running untrusted workflows on such a machine.
