import base64
from io import BytesIO

import aiohttp
import comfy.model_management
from comfy_api.latest import ComfyAPISync, io
from PIL import Image

from .. import config
from ..providers import client, context, memory as conversation_memory, stats
from .config import LLMModelConfigType
from .context import LLMContextManagementType
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
                LLMContextManagementType.Input("context_management", optional=True),
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
                io.String.Output("stats", tooltip="Run usage and timing; unavailable metrics are shown as N/A."),
            ],
        )

    @classmethod
    async def execute(cls, model, system_prompt, prompt, image=None, model_config=None, vision="auto",
                      reasoning_effort="medium", seed=-1, memory=False, sessions="",
                      context_management=None) -> io.NodeOutput:
        run_stats = stats.RunStats((model_config or {}).get("max_context", -1),
                                   context.model_scope(model, reasoning_effort))
        if memory:
            async with conversation_memory.conversation("chat", sessions) as entry:
                return await cls._execute(model, system_prompt, prompt, image, model_config, vision,
                                          reasoning_effort, seed, entry, context_management, run_stats)
        return await cls._execute(model, system_prompt, prompt, image, model_config, vision,
                                  reasoning_effort, seed, None, context_management, run_stats)

    @classmethod
    def fingerprint_inputs(cls, memory=False, seed=-1, **kwargs):
        return conversation_memory.fingerprint(memory, seed)

    @classmethod
    async def _execute(cls, model, system_prompt, prompt, image, model_config, vision,
                       reasoning_effort, seed, entry, context_management=None, run_stats=None):
        run_stats = run_stats or stats.RunStats((model_config or {}).get("max_context", -1),
                                               context.model_scope(model, reasoning_effort))
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
        max_tokens = sampling.get("max_output_tokens", sampling.get("max_tokens", -1))

        api = ComfyAPISync()

        def on_chunk(text_len):
            if max_tokens > 0:
                api.execution.set_progress(text_len, max_tokens)

        api_key = client.resolve_key(base_url)
        timeout = aiohttp.ClientTimeout(total=sampling.get("timeout_seconds", 300))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            compactor = None
            if context_management and context_management.get("enabled"):
                if not system_prompt:
                    messages.insert(0, {"role": "system", "content": ""})
                compactor = context.Compactor(context_management, sampling,
                                              entry.get("context_checkpoint") if entry else None,
                                              entry.get("context_usage_anchor") if entry else None,
                                              run_stats.model_key)

                async def summarize(summary_messages, allowance, tools):
                    summary_payload = client.build_chat_payload(model_id, summary_messages, sampling, seed)
                    summary_payload["max_tokens"] = allowance
                    summary, _ = await client.chat_stream(
                        session, base_url, api_key, summary_payload, lambda _: None,
                        should_interrupt=comfy.model_management.processing_interrupted,
                        stats=run_stats, compaction=True)
                    comfy.model_management.throw_exception_if_processing_interrupted()
                    return summary

                payload["messages"] = await compactor.prepare(messages, summarize)
            text, reasoning = await client.chat_stream(
                session, base_url, api_key, payload, on_chunk,
                should_interrupt=comfy.model_management.processing_interrupted,
                stats=run_stats,
            )
        comfy.model_management.throw_exception_if_processing_interrupted()
        if entry is not None:
            if compactor is not None:
                entry["context_checkpoint"] = compactor.checkpoint
                entry["context_usage_anchor"] = run_stats.last_anchor()
            assistant = {"role": "assistant", "content": text}
            if reasoning:
                assistant["reasoning_content"] = reasoning
            conversation_memory.append(entry, [
                {"role": "user", "content": content},
                assistant,
            ])
            entry.setdefault("runs", []).append({"system_prompt": system_prompt, "model": model_id,
                                                  "reasoning": reasoning})
        return io.NodeOutput(text, reasoning, run_stats.render())
