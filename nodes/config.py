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
                    tooltip="-1 = provider default",
                ),
                io.Float.Input(
                    "top_p",
                    default=-1.0,
                    min=-1.0,
                    max=1.0,
                    step=0.01,
                    tooltip="-1 = provider default",
                ),
                io.Int.Input("top_k", default=-1, min=-1, max=1000, tooltip="-1 = provider default"),
                io.Int.Input(
                    "max_tokens",
                    default=-1,
                    min=-1,
                    max=1000000,
                    tooltip="-1 = provider default",
                ),
                io.String.Input(
                    "reasoning_effort",
                    default="",
                    tooltip="Empty = provider default. Provider-specific levels only, e.g. minimal, low, medium, high, xhigh",
                ),
            ],
            outputs=[LLMModelConfigType.Output("model_config")],
        )

    @classmethod
    def execute(cls, temperature, top_p, top_k, max_tokens, reasoning_effort) -> io.NodeOutput:
        return io.NodeOutput(
            {
                "temperature": temperature,
                "top_p": top_p,
                "top_k": top_k,
                "max_tokens": max_tokens,
                "reasoning_effort": reasoning_effort,
            }
        )
