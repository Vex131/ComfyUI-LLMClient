import aiohttp
from comfy_api.latest import io

from .. import config
from ..providers import client
from .provider import LLMProviderType


class LLMUnload(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="LLMUnload",
            display_name="LLM Unload",
            category="LLM Client",
            inputs=[
                LLMProviderType.Input("model"),
                io.Combo.Input(
                    "mode",
                    options=["offload_vram_to_ram", "release_all", "wake_up"],
                    default="offload_vram_to_ram",
                ),
            ],
            outputs=[io.String.Output("status")],
        )

    @classmethod
    async def execute(cls, model, mode) -> io.NodeOutput:
        base_url = model["base_url"]
        entry = config.cached_entry(base_url)
        profile = entry["profile"] if entry else "generic"

        async with aiohttp.ClientSession() as session:
            status = await client.unload_command(
                session, base_url, model.get("api_key", ""), profile, mode, model.get("model", "")
            )
        return io.NodeOutput(status)
