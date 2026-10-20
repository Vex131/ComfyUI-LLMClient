# unload: "sleep" = vLLM sleep endpoints (server needs --enable-sleep-mode and
# VLLM_SERVER_DEV_MODE=1); "router" = llama.cpp router mode only (--models-dir),
# detected via GET /models entries carrying a status field; None = no unload API.
# probe() only ever assigns llamacpp / vllm / generic; anything else is manual.
CAPABILITIES = {
    "generic": {"unload": None},
    "llamacpp": {"unload": "router"},
    "vllm": {"unload": "sleep"},
    "openai": {"unload": None},
}
