# ComfyUI-LLMClient

Use a local or hosted LLM in your ComfyUI workflows through an
OpenAI-compatible API. Generate or improve prompts, analyze images with a
vision model, or use an agent to research and inspect your local models.
The resulting text can connect to any compatible string input in your graph.

Works with compatible servers such as llama.cpp, vLLM, NInfer, Strata and
OpenAI. You need a running API server or a hosted provider; this extension
does not run the chat model itself. Optional document search requires additional
dependencies and a local embedding model.

## Install

Clone this repository into your ComfyUI `custom_nodes` directory, then
restart ComfyUI and refresh your browser:

```bash
git clone https://github.com/Vex131/ComfyUI-LLMClient.git
```

You can also use ComfyUI Manager's install-from-Git-URL option with the
same repository URL.

## Quick start

1. Add **LLM Provider** and enter your server's API base URL, for example
   `http://127.0.0.1:8080/v1`. Click **Test Connection** and choose a model.
2. If your server requires an API key, open **Manage API Keys** on the
   provider node and save it for that URL. Otherwise, skip this step.
3. Connect the provider to **LLM Chat**, enter a prompt, and connect its
   `output` to another text input, such as CLIP Text Encode.
4. Optionally connect an image to LLM Chat or LLM Agent when using a
   vision-capable model.

## Nodes

| Node | Purpose |
| --- | --- |
| **LLM Provider** | Connect to a server and select a model. |
| **LLM Chat** | Send a prompt and optional image to the model. |
| **LLM Agent** | Let the model use tools to answer a task. |
| **LLM Agent Config** | Set the agent's instructions, output format and tools. |
| **Load Documents** | Select a document file or folder for the agent. |
| **LLM RAG Config** | Choose the embedding model and retrieval settings. |
| **LLM Model Config** | Set optional sampling parameters and request timeout. |
| **LLM Pattern Extract** | Extract labeled fields from JSON or text. |
| **LLM Unload** | Offload, unload or wake models on supported servers. |

## Document search

Install the optional RAG dependencies into ComfyUI's Python environment with
`pip install "sentence-transformers>=3.4,<6" "pypdf>=5,<7" "python-docx>=1.1,<2"`.
Add **Load Documents**, then connect its `documents` output to **LLM Agent**.
Use **Choose document to upload** or **Choose folder to upload** for files on
your browser's computer. Alternatively, enter a file or folder on the ComfyUI
server in `document_path` (folders are scanned recursively). Connect **LLM RAG Config**
to **LLM Agent Config**'s `rag_config` input. In LLM RAG Config choose
`Local path / local model` and enter a local Sentence Transformers directory,
select a detected model, or select a preset and click **Download selected
embedding model**. Preset parameter counts and download sizes are approximate.
Downloads are explicit and stored in `models/llm_embeddings`. Compatible model
directories in `models/llm_embeddings` and `models/embeddings` are detected on
startup; standalone ComfyUI textual-inversion embeddings are not RAG models.
The download button reports progress, and completed presets appear with a
`| Downloaded` at the end of their model-menu entry after refreshing the browser.
The embedding model runs on CPU. `top_k` controls the number of retrieved
passages. Workflows using the previous loader's embedding settings must add and
connect LLM RAG Config.
Supported formats are text, Markdown, JSON, CSV, TSV, XML, HTML, YAML, logs,
text-based PDF and DOCX. Legacy DOC and image-only/scanned PDF are not
supported; OCR scanned PDFs first. UTF-8 is required for text files.
Files are limited to 20 MB each, with at most 500 files and 10,000 chunks.
Indexes and document embeddings are cached in memory and reused across agent
runs. The loader checks document contents, and the agent reindexes when files
are added, removed or changed, or the embedding model changes. The cache holds
up to two indexes and is cleared on restart; the initial indexing can take time.
Retrieved passages and source paths are sent to the
configured chat provider, which may be remote; only index documents you trust
the provider to receive. Document content is untrusted and can contain
misleading instructions. LLM Chat does not have tool access.

## Conversation memory

Enable `memory` on LLM Chat or LLM Agent to automatically create a session.
Use the **session menu** to switch among that node's saved sessions or choose
**New session...**. Sessions use UTC timestamps with milliseconds (for example,
`2026-11-24T12:34:56.123Z`) and are ordered by most recent activity. The menu
is hidden when memory is off; disabling memory does not erase sessions. The
**×** next to a saved session permanently deletes its conversation after
confirmation; deleting the active session selects a new one. Separately saved
output files are not deleted. An empty deletion marker remains to prevent a
previously queued run from recreating the deleted session.
Each node has a separate session owner. Duplicated nodes in the same workflow
receive new owners; copying an entire workflow also copies its session IDs.

Full sessions, including messages, image data, agent tool calls, tool results
and reasoning, are stored unencrypted under `ComfyUI/user/llmclient/sessions`.
Anyone with access to those files can read them. Prior messages are sent to the
currently selected provider, which may be remote. Session data is not embedded
in the workflow, but its session identifier is.

With memory on and a fixed seed, an unchanged workflow can reuse the cached
result without appending a turn. ComfyUI's cache keys use workflow inputs and
the session ID, not the current contents of the session file; returning to a
previously cached prompt can therefore reuse its old result. A seed of `-1`
forces a new run. Context
overflow handling is not implemented: no compaction or automatic truncation
occurs. `max_context` on LLM Model Config is reserved for future context
handling and does not alter the provider's configuration.

Chat and Agent default `reasoning_effort` to `medium`, including if the field is
blank. When the provider advertises reasoning modes in its model metadata, use
**Test Connection** on LLM Provider to refresh them; the node offers a selector
for the advertised modes. Otherwise, enter a provider-supported value manually.

## Documentation

- [Node reference, API keys, web search and server support](docs/reference.md)

API keys are managed outside workflows. Read the
[security notes](docs/reference.md#credentials) before using a hosted
provider or exposing ComfyUI to a network.

## License

[MIT](LICENSE)
