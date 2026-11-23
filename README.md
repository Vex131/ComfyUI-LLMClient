# ComfyUI-LLMClient

Use a local or hosted LLM in your ComfyUI workflows through an
OpenAI-compatible API. Generate or improve prompts, analyze images with a
vision model, or use an agent to research and inspect your local models.
The resulting text can connect to any compatible string input in your graph.

Works with compatible servers such as llama.cpp, vLLM, NInfer, Strata and
OpenAI. You need a running API server or a hosted provider; this extension
does not run the model itself. It uses dependencies already included with
ComfyUI.

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
| **LLM Model Config** | Set optional sampling parameters and request timeout. |
| **LLM Pattern Extract** | Extract labeled fields from JSON or text. |
| **LLM Unload** | Offload, unload or wake models on supported servers. |

## Documentation

- [Node reference, API keys, web search and server support](docs/reference.md)

API keys are managed outside workflows. Read the
[security notes](docs/reference.md#credentials) before using a hosted
provider or exposing ComfyUI to a network.

## License

[MIT](LICENSE)
