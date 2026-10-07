import aiohttp
from comfy_api.latest import io

from .. import config
from ..providers import client, profiles

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
                    options=profiles.PROFILE_OPTIONS,
                    default="auto",
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
                io.String.Output("info"),
            ],
        )

    @classmethod
    async def execute(cls, base_url, api_key, profile, model) -> io.NodeOutput:
        provider = {"base_url": base_url, "api_key": api_key, "profile": profile, "model": model}
        entry = config.cached_entry(base_url)
        if entry is None:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                result = await client.probe(session, base_url, api_key)
            if not result["ok"]:
                return io.NodeOutput(provider, "Provider unreachable: {}".format(result["error"]))
            detected = profile if profile != "auto" else result["profile"]
            config.update_cache(base_url, detected, result["models"], context=result.get("context"))
            entry = config.cached_entry(base_url)
        return io.NodeOutput(provider, profiles.format_info(entry))
