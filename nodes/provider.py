import aiohttp
from comfy_api.latest import io

from .. import config
from ..providers import client

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
                    tooltip="OpenAI-compatible API root. /v1 is appended if missing. "
                            "Optional API keys are stored outside the workflow using the "
                            "Manage API Keys button and matched to this URL at run time.",
                ),
                io.Combo.Input(
                    "model",
                    options=models,
                    default=models[0],
                    tooltip="Use the Test Connection button to refresh.",
                ),
            ],
            outputs=[
                LLMProviderType.Output("model"),
            ],
        )

    @classmethod
    async def execute(cls, base_url, model) -> io.NodeOutput:
        provider = {"base_url": base_url, "model": model}
        if config.cached_entry(base_url) is None:
            key = client.resolve_key(base_url)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                result = await client.probe(session, base_url, key)
            if result["ok"]:
                config.update_cache(base_url, result["profile"], result["models"], context=result.get("context"))
        return io.NodeOutput(provider)
