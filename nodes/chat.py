import base64
from io import BytesIO

import aiohttp
from comfy_api.latest import ComfyAPISync, io
from PIL import Image

from .. import config
from ..providers import client
from .config import LLMModelConfigType
from .provider import LLMProviderType


def _image_data_url(image) -> str:
    array = (image.cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
    buffer = BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class LLMChat(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="LLMChat",
            display_name="LLM Chat",
            category="LLM Client",
            inputs=[
                LLMProviderType.Input("provider"),
                io.String.Input("system_prompt", multiline=True, default="", optional=True),
                io.String.Input("prompt", multiline=True, default=""),
                io.Image.Input("image", optional=True),
                LLMModelConfigType.Input("model_config", optional=True, tooltip="Unconnected = provider defaults"),
                io.Combo.Input(
                    "vision",
                    options=["auto", "yes", "no"],
                    default="auto",
                    advanced=True,
                    tooltip="Override vision detection for this model",
                ),
                io.Int.Input("seed", default=-1, min=-1, max=2147483647, control_after_generate=True, advanced=True),
            ],
            outputs=[
                io.String.Output("output"),
                io.String.Output("reasoning"),
            ],
        )

    @classmethod
    async def execute(cls, provider, system_prompt, prompt, image=None, model_config=None, vision="auto",
                      seed=-1) -> io.NodeOutput:
        base_url = provider["base_url"]
        api_key = provider.get("api_key", "")
        model = provider["model"]

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        content = prompt
        if image is not None:
            if vision == "no":
                raise ValueError("Vision override is set to 'no' but an image is connected.")
            if vision == "auto":
                for entry in config.cached_models(base_url):
                    if entry.get("id") == model and entry.get("vision") is False:
                        raise ValueError(
                            "Selected model '{}' does not support image input. Set the vision override to 'yes' to force it.".format(model)
                        )
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _image_data_url(image[0])}},
            ]
        messages.append({"role": "user", "content": content})

        payload = client.build_chat_payload(model, messages, model_config, seed)
        max_tokens = model_config["max_tokens"] if model_config else -1

        api = ComfyAPISync()

        def on_chunk(text_len):
            if max_tokens > 0:
                api.execution.set_progress(text_len, max_tokens)

        async with aiohttp.ClientSession() as session:
            text, reasoning = await client.chat_stream(session, base_url, api_key, payload, on_chunk)
        return io.NodeOutput(text, reasoning)
