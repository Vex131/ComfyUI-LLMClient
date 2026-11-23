from comfy_api.latest import io

LLMModelConfigType = io.Custom("LLM_MODEL_CONFIG")


class LLMModelConfig(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="LLMModelConfig",
            display_name="LLM Model Config",
            category="LLM Client",
            inputs=[
                io.Float.Input(
                    "temperature",
                    default=-1.0,
                    min=-1.0,
                    max=4.0,
                    step=0.01,
                    tooltip="Sampling randomness; lower values are more predictable. -1 uses the provider default.",
                ),
                io.Float.Input(
                    "top_p",
                    default=-1.0,
                    min=-1.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Sample from the most likely tokens up to this cumulative probability. -1 uses the provider default.",
                ),
                io.Int.Input("top_k", default=-1, min=-1, max=1000,
                             tooltip="Limit sampling to this many candidate tokens. -1 uses the provider default."),
                io.Int.Input(
                    "max_tokens",
                    default=-1,
                    min=-1,
                    max=1000000,
                    tooltip="Maximum generated tokens per request, not the context window. -1 uses the provider default.",
                ),
                io.Int.Input(
                    "max_context",
                    default=-1,
                    min=-1,
                    max=10000000,
                    optional=True,
                    advanced=True,
                    tooltip="Context-window size in tokens; -1 means unknown. Reserved for future context handling; does not change the provider.",
                ),
                io.Int.Input(
                    "timeout_seconds",
                    default=300,
                    min=1,
                    max=86400,
                    tooltip="Maximum seconds for each complete model request, including streaming",
                ),
            ],
            outputs=[LLMModelConfigType.Output("model_config", tooltip="Connect to Chat or Agent to apply these settings.")],
        )

    @classmethod
    def execute(cls, temperature, top_p, top_k, max_tokens, timeout_seconds=300, max_context=-1) -> io.NodeOutput:
        return io.NodeOutput(
            {
                "temperature": temperature,
                "top_p": top_p,
                "top_k": top_k,
                "max_tokens": max_tokens,
                "timeout_seconds": timeout_seconds,
                "max_context": max_context,
            }
        )
