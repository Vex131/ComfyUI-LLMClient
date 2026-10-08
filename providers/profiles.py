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

_UNLOAD_LABELS = {
    "sleep": "vLLM sleep/wake (level 1/2)",
    "router": "llama.cpp router load/unload",
}


def format_info(entry: dict) -> str:
    lines = ["Profile: {}".format(entry["profile"])]
    if entry.get("context"):
        lines.append("Context: {} tokens".format(entry["context"]))
    capability = CAPABILITIES.get(entry["profile"], {}).get("unload")
    lines.append("Unload: " + _UNLOAD_LABELS.get(capability, "not supported"))
    lines.append("Models:")
    for model in entry.get("models", []):
        flags = []
        if model.get("vision") is True:
            flags.append("vision")
        elif model.get("vision") is False:
            flags.append("text-only")
        if model.get("reasoning"):
            flags.append("reasoning")
        suffix = " [{}]".format(", ".join(flags)) if flags else ""
        lines.append("  - {}{}".format(model.get("id"), suffix))
    return "\n".join(lines)
