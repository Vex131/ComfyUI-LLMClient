# ComfyUI-LLMClient

Use a local or hosted LLM in ComfyUI through an OpenAI-compatible API.
Chat, analyze images, search documents, and use an agent with web and local
model tools. Connect the text output to other nodes in your workflow.

Compatible servers include llama.cpp, vLLM, NInfer, Strata, and OpenAI.
You need a running server or hosted provider; this extension does not run
the chat model itself.

## Install

### ComfyUI Manager

Find **ComfyUI-LLMClient** in Manager and install it, or install from this
Git URL:

```text
https://github.com/Vex131/ComfyUI-LLMClient
```

Manager installs the declared Python dependencies during installation.
Restart ComfyUI and refresh the browser after installation. If installation
reports dependency errors, use Manager's dependency repair or install them
manually as described below.

### Manual installation

Clone into your ComfyUI `custom_nodes` directory:

```bash
git clone https://github.com/Vex131/ComfyUI-LLMClient.git
```

Install the dependencies using **the Python environment that runs ComfyUI**:

```bash
python -m pip install -r ComfyUI-LLMClient/requirements.txt
```

Run this command from `custom_nodes`, replacing `python` with ComfyUI's
Python executable if needed. For Windows Portable, from `custom_nodes`:

```powershell
..\..\python_embeded\python.exe -m pip install -r ComfyUI-LLMClient\requirements.txt
```

Restart ComfyUI and refresh the browser. Installing dependencies may download
large packages, including the document-search dependencies. A restart alone
does not install dependencies after a manual clone.

## Quick start

1. Add **LLM Provider**, enter your server URL (for example
   `http://127.0.0.1:8080/v1`), and select **Test Connection**.
2. Choose a model. If the server needs a key, add it through **Manage API
   Keys** on the provider node.
3. Connect the provider to **LLM Chat**, enter a prompt, and connect its
   `output` to a text input in your workflow.
4. Optionally connect an image when using a vision-capable model.

Responses are available when generation finishes; text is not displayed live.

## Nodes

| Node | Purpose |
| --- | --- |
| **LLM Provider** | Connect to a server and select a model. |
| **LLM Chat** | Send text and an optional image. |
| **LLM Agent** | Let the model use tools to complete a task. |
| **LLM Agent Config** | Choose agent instructions and tools. |
| **Load Documents** | Select documents for agent search. |
| **LLM RAG Config** | Choose the embedding model and search settings. |
| **LLM Model Config** | Set sampling parameters and request timeout. |
| **LLM Pattern Extract** | Extract labeled fields from JSON or text. |
| **LLM Unload** | Release or load models on supported servers. |
| **Free ComfyUI Models** | Release ComfyUI-managed models and pass data through. |

See the [node reference](docs/reference.md) for settings, credentials, and
server support.

## Search documents

1. Add **Load Documents** and select a file or folder on the ComfyUI server,
   or use its upload buttons.
2. Add **LLM RAG Config** and select a local Sentence Transformers model or
   download a preset. Downloads are saved under `ComfyUI/models/llm_embeddings`.
3. Connect RAG Config to **LLM Agent Config**, and connect the documents and
   agent config to **LLM Agent**.

Supported files include text, Markdown, JSON, CSV, HTML, PDF, and DOCX.
PDFs must contain extractable text; scanned pages need OCR. Text files must
be UTF-8. Limits are 20 MB per file, 500 files, and 10,000 chunks.
Embedding runs on CPU; the first indexing run can take time.

Only matching passages are sent to the chat provider. A remote provider
receives those passages and their source paths. Documents may contain
misleading instructions, so use trusted sources and review the results.

## Conversation memory

Enable `memory` on Chat or Agent to save and continue conversations. Use the
session menu to switch, create, or delete sessions. Disabling memory does not
delete them.

Sessions are stored **unencrypted** in `ComfyUI/user/llmclient/sessions` and
may include prompts, images, tool results, and reasoning. Previous messages are
sent to the selected provider. Session contents are not saved in workflows,
but session identifiers are.

Long conversations are not automatically shortened. With a fixed seed,
ComfyUI may reuse a cached result; use `-1` to force a new run.

## Unload models

Connect **LLM Unload** to the provider. Connect Chat's output through its
optional `text` input when unloading should happen after generation.

- **Strata:** `release_all` unloads the model. The next request loads it
  again; `wake_up` loads it in advance. RAM-only offload is not supported.
- **llama.cpp:** load and unload are available in router mode.
- **vLLM:** supports sleep and wake when sleep mode is enabled.

See [unload support](docs/reference.md#unload-support) for requirements.

To move from image or video generation back to an LLM, place **Free ComfyUI
Models** after the last image/video model operation. It passes any connected
data through unchanged after requesting ComfyUI model unload and cache
cleanup. Choose `free_vram` or `free_vram_and_ram`, which also collects
unreferenced Python objects. The RAM mode does not clear the workflow cache;
model weights still referenced by the workflow may remain in RAM. Connect its
output to the next stage to enforce the order. Its `status` output can also be
used to establish a dependency.

This node does not affect external servers such as Strata and cannot guarantee
release of memory held outside ComfyUI's model manager. Later ComfyUI nodes can
reload the models when needed.

## Privacy and security

API keys are stored outside workflows. Review the
[credential notes](docs/reference.md#credentials), especially before exposing
ComfyUI on a network or importing old workflows that contained API keys.

The agent's web fetch is limited to public internet addresses and does not
follow redirects. This restriction does not affect configured local LLM or
SearXNG servers. Web pages and document contents are untrusted: review agent
outputs and disable tools you do not need.

## License

[MIT](LICENSE)
