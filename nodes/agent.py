import json

import aiohttp
import comfy.model_management
from comfy_api.latest import ComfyAPISync, io

from ..providers import agent, webfetch, websearch
from . import assets
from .agent_config import LLMAgentConfigType
from .config import LLMModelConfigType
from .provider import LLMProviderType

DEFAULT_SYSTEM = (
    "You are a capable agent running inside a ComfyUI workflow. Use the provided tools whenever "
    "they improve the answer, and finish with a complete, self-contained final answer."
)

DEFAULT_AGENT_CONFIG = {
    "system_prompt": "",
    "output_format": "text",
    "json_hint": "",
    "assets_tool": True,
    "web_tool": True,
    "tool_mode": "auto",
    "max_iterations": 8,
}


class LLMAgent(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="LLMAgent",
            display_name="LLM Agent",
            category="LLM Client",
            inputs=[
                LLMProviderType.Input("model"),
                LLMAgentConfigType.Input(
                    "agent_config",
                    optional=True,
                    tooltip="Unconnected = generic agent with all tools enabled (use LLM Agent Config to "
                            "set a purpose, output format and tools)",
                ),
                io.String.Input("prompt", multiline=True, default="",
                                tooltip="The task for the agent"),
                LLMModelConfigType.Input("model_config", optional=True,
                                         tooltip="Unconnected = provider defaults"),
                io.String.Input(
                    "reasoning_effort",
                    default="",
                    advanced=True,
                    tooltip="Empty = provider default. Provider-specific levels only, e.g. minimal, low, medium, high, xhigh",
                ),
                io.Int.Input("seed", default=-1, min=-1, max=2147483647, control_after_generate=True,
                             advanced=True),
            ],
            outputs=[
                io.String.Output("output", tooltip="Final answer of the run"),
                io.String.Output("reasoning", tooltip="Thinking trace across turns, when the model emits one"),
                io.String.Output(
                    "session",
                    tooltip="Full transcript (JSON): every assistant turn, tool call and tool result",
                ),
            ],
        )

    @classmethod
    async def execute(cls, model, prompt, agent_config=None, model_config=None, reasoning_effort="",
                      seed=-1) -> io.NodeOutput:
        base_url = model["base_url"]
        api_key = model.get("api_key", "")
        model_id = model["model"]

        cfg = dict(DEFAULT_AGENT_CONFIG)
        if agent_config:
            cfg.update(agent_config)

        system = cfg["system_prompt"] or DEFAULT_SYSTEM
        if cfg["output_format"] == "json":
            system += "\nRespond with ONLY a single valid JSON object as your final answer."
            if cfg["json_hint"]:
                keys = ", ".join(k.strip() for k in cfg["json_hint"].split(",") if k.strip())
                if keys:
                    system += " Required keys: {}.".format(keys)

        messages = [{"role": "system", "content": system}]
        messages.append({"role": "user", "content": prompt})

        def list_tool(args, session):
            return assets.list_assets(args.get("category", "all"))

        def details_tool(args, session):
            return assets.get_asset_details(args.get("category", "all"), str(args.get("name", "")))

        async def web_tool_impl(args, session):
            return await webfetch.fetch_text(session, args.get("url", ""))

        async def web_search_impl(args, session):
            return await websearch.search(session, args.get("query", ""))

        tool_impls = {}
        if cfg["assets_tool"]:
            tool_impls["list_local_models"] = list_tool
            tool_impls["get_model_details"] = details_tool
        if cfg["web_tool"]:
            tool_impls["web_fetch"] = web_tool_impl
            tool_impls["web_search"] = web_search_impl

        sampling = dict(model_config) if model_config else {}
        if reasoning_effort:
            sampling["reasoning_effort"] = reasoning_effort
        max_tokens = sampling.get("max_tokens", -1)
        api = ComfyAPISync()

        def on_chunk(text_len):
            if max_tokens > 0:
                api.execution.set_progress(text_len, max_tokens)

        async with aiohttp.ClientSession() as session:
            result = await agent.run_loop(
                session, base_url, api_key, model_id, messages,
                sampling=sampling,
                seed=seed,
                tool_impls=tool_impls,
                max_iterations=cfg["max_iterations"],
                tool_mode=cfg["tool_mode"],
                on_chunk=on_chunk,
                should_interrupt=comfy.model_management.processing_interrupted,
            )
        comfy.model_management.throw_exception_if_processing_interrupted()
        return io.NodeOutput(
            result["final"],
            result["reasoning"],
            json.dumps(result["messages"], indent=2, ensure_ascii=False),
        )
