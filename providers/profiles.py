PROFILE_OPTIONS = ["auto", "generic", "llamacpp", "vllm", "ninfer", "strata", "openai"]

# unload: "sleep" = vLLM sleep endpoints (server needs --enable-sleep-mode and
# VLLM_SERVER_DEV_MODE=1); "router" = llama.cpp router mode only (--models-dir),
# detected via GET /models entries carrying a status field; None = no unload API.
CAPABILITIES = {
    "generic": {"unload": None},
    "llamacpp": {"unload": "router"},
    "vllm": {"unload": "sleep"},
    "ninfer": {"unload": None},
    "strata": {"unload": None},
    "openai": {"unload": None},
}
