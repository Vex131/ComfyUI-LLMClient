# ComfyUI-LLMClient

LLM nodes for ComfyUI over any OpenAI-compatible API (llama.cpp, vLLM,
NInfer, Strata, OpenAI, ...): prompt generation and enhancement, image
captioning and analysis, text extraction — `output` routes to any string
socket in your graph.

No new dependencies: aiohttp, Pillow, and torch already ship with ComfyUI.

## Install

- **ComfyUI-Manager**: search `ComfyUI-LLMClient`, or install via git URL
  `https://github.com/Vex131/ComfyUI-LLMClient`
- **comfy-cli**: `comfy node install comfyui-llmclient`
- **Manual**: clone into `ComfyUI/custom_nodes/` and restart

Requires a running OpenAI-compatible server, e.g.:

```
llama-server -m Qwen3-8B-Q4_K_M.gguf --port 8080
```

## Quick start

1. **LLM Provider**: set `base_url` (`http://127.0.0.1:8080/v1`; `/v1` is
   appended if missing), click **Test Connection**, pick a model.
2. **LLM Chat**: connect the provider's `model` output, write a prompt.
3. Wire `output` wherever you need it — CLIP Text Encode, Save Text, etc.

Cancel stops generation on the server too, not just in ComfyUI.

## Nodes

### LLM Provider

- `base_url` — OpenAI-compatible API root; `/v1` appended if missing.
- `api_key` — optional; stored in the workflow JSON (see warning).
- `model` — combo filled by **Test Connection**. The probe auto-detects the
  server profile (llama.cpp / vLLM / generic) and caches models, context
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

Streams the reply.

- `prompt`, optional `system_prompt`, optional `image`.
- `vision` — `auto` rejects images for models detected as text-only; `yes`
  forces it.
- `model_config` — optional sampling overrides (see LLM Model Config).
- `reasoning_effort` — empty = provider default; provider-specific levels
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
- `web_tool` — `web_search` (DuckDuckGo, no key needed) and `web_fetch`:
  search the web, then read the full page of a promising result — model
  cards, trigger words, docs. If DuckDuckGo blocks the request the tool
  reports the failure to the model instead of failing the run.
- `tool_mode` (advanced) — `auto` uses native OpenAI tool-calling and falls
  back to a JSON text protocol when the server rejects tools. `native`
  requires support (llama.cpp: `--jinja`; vLLM:
  `--enable-auto-tool-choice --tool-call-parser ...`). `json` uses the text
  protocol directly.
- `max_iterations` (advanced) — agent loop cap, default 8.

### LLM Agent

Agentic loop: model → tool calls → results → ... until a final answer.
Streams each turn; Cancel works like LLM Chat.

- `model`, optional `agent_config` (unconnected = generic agent, all tools
  on), `prompt`, optional `model_config`, `reasoning_effort`, `seed`.
- Outputs: `output` (final answer), `reasoning`, and `session` — the full
  transcript (JSON) of every assistant turn, tool call and tool result.
  Wire `output`/`session` to any text viewer.

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

## API key warning

The API key is a workflow input: when set, it is **stored in the workflow
JSON**. Anyone you share the workflow with can read it.

## Roadmap

Planned: RAG over local documents, document/text input nodes, API-key web
search backend (Tavily/Brave/Serper-style) as a more reliable alternative
to DuckDuckGo scraping.
