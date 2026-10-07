import base64
from io import BytesIO

import aiohttp
from comfy_api.latest import ComfyAPISync, io
from PIL import Image

from .. import config
from ..providers import client
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
                io.Float.Input(
                    "temperature",
                    default=-1.0,
                    min=-1.0,
                    max=4.0,
                    step=0.01,
                    advanced=True,
                    tooltip="-1 = provider default",
                ),
                io.Float.Input(
                    "top_p",
                    default=-1.0,
                    min=-1.0,
                    max=1.0,
                    step=0.01,
                    advanced=True,
                    tooltip="-1 = provider default",
                ),
                io.Int.Input("top_k", default=-1, min=-1, max=1000, advanced=True, tooltip="-1 = provider default"),
                io.Int.Input(
                    "max_tokens",
                    default=-1,
                    min=-1,
                    max=1000000,
                    advanced=True,
                    tooltip="-1 = provider default",
                ),
                io.Combo.Input(
                    "reasoning_effort",
                    options=["default", "minimal", "low", "medium", "high"],
                    default="default",
                    advanced=True,
                ),
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
    async def execute(cls, provider, system_prompt, prompt, image=None, temperature=-1.0, top_p=-1.0, top_k=-1,
                      max_tokens=-1, reasoning_effort="default", vision="auto", seed=-1) -> io.NodeOutput:
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

        payload = {"model": model, "messages": messages}
        if temperature >= 0:
            payload["temperature"] = temperature
        if top_p >= 0:
            payload["top_p"] = top_p
        if top_k >= 0:
            payload["top_k"] = top_k
        if max_tokens >= 0:
            payload["max_tokens"] = max_tokens
        if seed >= 0:
            payload["seed"] = seed
        if reasoning_effort != "default":
            payload["reasoning_effort"] = reasoning_effort

        api = ComfyAPISync()

        def on_chunk(text_len):
            if max_tokens > 0:
                api.execution.set_progress(text_len, max_tokens)

        async with aiohttp.ClientSession() as session:
            text, reasoning = await client.chat_stream(session, base_url, api_key, payload, on_chunk)
        return io.NodeOutput(text, reasoning)
