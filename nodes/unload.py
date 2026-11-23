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
                LLMProviderType.Input("model", tooltip="Provider and model to control; supported operations depend on the server."),
                io.Combo.Input(
                    "mode",
                    options=["offload_vram_to_ram", "release_all", "wake_up"],
                    default="offload_vram_to_ram",
                    tooltip="Offload model weights, release model resources, or wake a sleeping provider.",
                ),
                io.String.Input("text", force_input=True, optional=True,
                                tooltip="Optional text to pass through after the unload operation."),
            ],
            outputs=[io.String.Output("status", tooltip="Result of the provider operation."),
                     io.String.Output("text", tooltip="The text passed into this node.")],
        )

    @classmethod
    async def execute(cls, model, mode, text="") -> io.NodeOutput:
        base_url = model["base_url"]
        entry = config.cached_entry(base_url)
        profile = entry["profile"] if entry else "generic"

        api_key = client.resolve_key(base_url)
        async with aiohttp.ClientSession() as session:
            status = await client.unload_command(
                session, base_url, api_key, profile, mode, model.get("model", "")
            )
        return io.NodeOutput(status, text)
