# ComfyUI-LLMClient

LLM client nodes for ComfyUI that talk to any OpenAI-API-style provider:
llama.cpp (llama-server), vLLM, NInfer, Strata, OpenAI, or any generic
OpenAI-compatible server. Connection test with model refresh, chat with
vision support, and a capability-based model unload/sleep node.

No new dependencies — uses aiohttp, Pillow, and torch already shipped with ComfyUI.

## Install

- Comfy Registry: pending publish (`comfy-cli install comfyui-llmclient` once live)
- ComfyUI-Manager: install via git URL `https://github.com/changeme/ComfyUI-LLMClient`
- Manual: clone into `ComfyUI/custom_nodes/ComfyUI-LLMClient` and restart

## Nodes (category: LLM Client)

### LLM Provider
Points at an OpenAI-compatible API root (e.g. `http://127.0.0.1:8080/v1`;
`/v1` is appended if missing). Optional API key, provider profile
(`auto` detects llama.cpp / vLLM / generic), and a model combo.
Use the **Test Connection** / **Refresh Models** buttons on the node to
probe the server and populate the model list. Detected profiles, models,
context size, and vision capability are cached in `providers.json` next
to `config.py`. Outputs the `model` handle for the other nodes plus an
`info` string (profile, context, unload support, per-model vision flags)
you can connect to a **Preview as Text** node. The cache is also filled
automatically on first execution.

### LLM Model Config
Optional sampling presets for LLM Chat: `temperature`, `top_p`, `top_k`,
`max_tokens`, `reasoning_effort` (all `-1`/`default` = provider default).
Leave the `config` input of LLM Chat unconnected to use provider defaults.

### LLM Chat
Sends system prompt + user prompt (optionally with an IMAGE input) through
the provider handle and streams the response. Outputs `output` and
`reasoning` (from `delta.reasoning_content` / `delta.reasoning`).
Optional `model_config` input from the LLM Model Config node. Advanced
`vision` override (`auto`/`yes`/`no`) and `seed`. With `auto`, a model
detected as non-multimodal rejects image input; force `yes` to send
images anyway.

### LLM Unload
Asks the server to release model memory. Modes: `offload_vram_to_ram`,
`release_all`, `wake_up`.

## Unload capability matrix

| Provider | Unload support | Requirements |
|----------|----------------|--------------|
| vLLM     | `/sleep?level=1` (weights to RAM), `/sleep?level=2` (discard), `/wake_up` | server started with `--enable-sleep-mode` and `VLLM_SERVER_DEV_MODE=1` |
| llama.cpp | `/models/unload`, `/models/load` (router mode only) | server in router mode (`--models-dir`) |
| Strata   | none | — |
| NInfer   | none | — |
| generic / OpenAI | none | — |

Unsupported profiles fail with a clear error naming the provider.

## API key warning

The API key is a workflow input: when set, it is **stored in the workflow
JSON**. Anyone you share the workflow with can read it.

## Example workflow

1. `LLM Provider`: base_url `http://127.0.0.1:8080/v1`, profile `auto`,
   click **Test Connection**, pick a model from the combo.
2. `Load Image` → `LLM Chat`: connect the `model` handle and the image,
   write a prompt like "Describe this image", read the `output` text.
3. When done, `LLM Unload` with mode `offload_vram_to_ram` to free VRAM
   (vLLM/llama.cpp router only).
