import base64
from io import BytesIO

import aiohttp
import comfy.model_management
from comfy_api.latest import ComfyAPISync, io
from PIL import Image

from .. import config
from ..providers import client, memory as conversation_memory
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
                LLMProviderType.Input("model", tooltip="Provider and model selected by LLM Provider."),
                io.String.Input("system_prompt", multiline=True, default="", optional=True,
                                tooltip="Optional instructions that set the model's role and behavior."),
                io.String.Input("prompt", multiline=True, default="", tooltip="Message to send to the model."),
                LLMModelConfigType.Input("model_config", optional=True, tooltip="Unconnected = provider defaults"),
                io.Image.Input("image", optional=True, tooltip="Optional image for a vision model; only the first image in a batch is used."),
                io.Combo.Input(
                    "vision",
                    options=["auto", "enabled", "disable"],
                    default="auto",
                    advanced=True,
                    tooltip="auto checks known capabilities; enabled forces image input; disable rejects connected images.",
                ),
                io.String.Input(
                    "reasoning_effort",
                    default="medium",
                    advanced=True,
                    tooltip="Reasoning effort; blank defaults to medium. Use a level supported by your provider.",
                ),
                io.Int.Input("seed", default=-1, min=-1, max=2147483647, control_after_generate=True, advanced=True,
                             tooltip="Sampling seed sent to the provider; -1 omits it. The provider may ignore seeds."),
                io.Boolean.Input("memory", default=False, optional=True,
                                 tooltip="Save and continue the selected session in ComfyUI's user directory."),
                io.String.Input("sessions", default="", advanced=True, optional=True,
                                tooltip="Selected session; use the sessions menu to create or switch sessions."),
            ],
            hidden=[io.Hidden.unique_id],
            outputs=[
                io.String.Output("output", tooltip="The model's response text."),
                io.String.Output("reasoning", tooltip="Reasoning text when the provider returns it; otherwise empty."),
            ],
        )

    @classmethod
    async def execute(cls, model, system_prompt, prompt, image=None, model_config=None, vision="auto",
                      reasoning_effort="medium", seed=-1, memory=False, sessions="") -> io.NodeOutput:
        if memory:
            async with conversation_memory.conversation("chat", sessions) as entry:
                return await cls._execute(model, system_prompt, prompt, image, model_config, vision,
                                          reasoning_effort, seed, entry)
        return await cls._execute(model, system_prompt, prompt, image, model_config, vision,
                                  reasoning_effort, seed, None)

    @classmethod
    def fingerprint_inputs(cls, memory=False, seed=-1, **kwargs):
        return conversation_memory.fingerprint(memory, seed)

    @classmethod
    async def _execute(cls, model, system_prompt, prompt, image, model_config, vision,
                       reasoning_effort, seed, entry):
        base_url = model["base_url"]
        model_id = model["model"]

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if entry is not None:
            messages.extend(conversation_memory.messages(entry))

        content = prompt
        if image is not None:
            if vision in ("disable", "no"):
                raise ValueError("Vision override is set to 'disable' but an image is connected.")
            if vision == "auto":
                for model_entry in config.cached_models(base_url):
                    if model_entry.get("id") == model_id and model_entry.get("vision") is False:
                        raise ValueError(
                            "Selected model '{}' does not support image input. Set the vision override to 'enabled' to force it.".format(model_id)
                        )
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _image_data_url(image[0])}},
            ]
        messages.append({"role": "user", "content": content})

        sampling = dict(model_config) if model_config else {}
        sampling["reasoning_effort"] = (reasoning_effort or "").strip() or "medium"

        payload = client.build_chat_payload(model_id, messages, sampling, seed)
        max_tokens = sampling.get("max_tokens", -1)

        api = ComfyAPISync()

        def on_chunk(text_len):
            if max_tokens > 0:
                api.execution.set_progress(text_len, max_tokens)

        api_key = client.resolve_key(base_url)
        timeout = aiohttp.ClientTimeout(total=sampling.get("timeout_seconds", 300))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            text, reasoning = await client.chat_stream(
                session, base_url, api_key, payload, on_chunk,
                should_interrupt=comfy.model_management.processing_interrupted,
            )
        comfy.model_management.throw_exception_if_processing_interrupted()
        if entry is not None:
            conversation_memory.append(entry, [
                {"role": "user", "content": content},
                {"role": "assistant", "content": text},
            ])
            entry.setdefault("runs", []).append({"system_prompt": system_prompt, "model": model_id,
                                                  "reasoning": reasoning})
        return io.NodeOutput(text, reasoning)
