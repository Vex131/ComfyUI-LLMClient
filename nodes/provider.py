from comfy_api.latest import io

from .. import config

LLMProviderType = io.Custom("LLM_PROVIDER")


class LLMProvider(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        models = config.all_cached_model_ids() or ["(run Test Connection)"]
        return io.Schema(
            node_id="LLMProvider",
            display_name="LLM Provider",
            category="LLM Client",
            inputs=[
                io.String.Input(
                    "base_url",
                    default="http://127.0.0.1:8080/v1",
                    tooltip="OpenAI-compatible API root. /v1 is appended if missing.",
                ),
                io.String.Input(
                    "api_key",
                    default="",
                    optional=True,
                    tooltip="Stored in the workflow when set.",
                ),
                io.Combo.Input(
                    "profile",
                    options=["auto", "generic", "llamacpp", "vllm", "ninfer", "strata", "openai"],
                    default="auto",
                ),
                io.Combo.Input(
                    "model",
                    options=models,
                    default=models[0],
                    tooltip="Use the Test Connection button to refresh.",
                ),
            ],
            outputs=[LLMProviderType.Output("provider")],
        )

    @classmethod
    def execute(cls, base_url, api_key, profile, model) -> io.NodeOutput:
        return io.NodeOutput({"base_url": base_url, "api_key": api_key, "profile": profile, "model": model})
