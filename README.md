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
- Outputs: `model` handle for LLM Chat / LLM Unload, and `info` text
  (profile, context, unload support, per-model vision flags).

### LLM Chat

Streams the reply.

- `prompt`, optional `system_prompt`, optional `image`.
- `vision` — `auto` rejects images for models detected as text-only; `yes`
  forces it.
- `model_config` — optional sampling overrides (see LLM Model Config).
- `seed` — `-1` = random.
- Outputs: `output`, and `reasoning` (thinking trace when the model emits
  one).

### LLM Model Config

Optional sampling overrides. `-1` = provider default for `temperature`,
`top_p`, `top_k`, `max_tokens`. `reasoning_effort` is free text, empty =
provider default; use only levels your server accepts (`low`, `medium`,
`high`, `xhigh`, ...) — invalid values are rejected by the server.

### LLM Unload

`offload_vram_to_ram` / `release_all` / `wake_up`. Support depends on the
server (matrix below); unsupported servers fail with a clear error.

## Unload support

| Server | What works | Requirements |
|--------|-----------|--------------|
| vLLM | sleep level 1 (weights to RAM), level 2 (discard), wake | `--enable-sleep-mode` and `VLLM_SERVER_DEV_MODE=1` |
| llama.cpp | `/models/load`, `/models/unload` | router mode (`--models-dir`) |

## API key warning

The API key is a workflow input: when set, it is **stored in the workflow
JSON**. Anyone you share the workflow with can read it.

## Roadmap

Planned: agentic loops and RAG, document/text input, tool use.
