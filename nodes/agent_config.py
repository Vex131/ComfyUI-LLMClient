from comfy_api.latest import io

LLMAgentConfigType = io.Custom("LLM_AGENT_CONFIG")


class LLMAgentConfig(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="LLMAgentConfig",
            display_name="LLM Agent Config",
            category="LLM Client",
            inputs=[
                io.String.Input(
                    "system_prompt",
                    multiline=True,
                    default="",
                    tooltip="The agent's purpose and rules. Empty = built-in generic agent instructions. "
                            "Write any task here: prompt authoring, code, summarization, research, ...",
                ),
                io.Combo.Input(
                    "output_format",
                    options=["text", "json"],
                    default="text",
                    tooltip="json forces a single JSON object final answer; feed it to LLM Pattern Extract",
                ),
                io.String.Input(
                    "json_hint",
                    default="",
                    tooltip="When output_format=json: comma-separated keys, e.g. "
                            "positive, negative, seed, steps, cfg, sampler, scheduler",
                ),
                io.Boolean.Input(
                    "assets_tool",
                    default=True,
                    tooltip="list_local_models / get_model_details: installed checkpoints, LoRA, embeddings "
                            "with embedded metadata (description cards, trigger words) and sidecar card files",
                ),
                io.Boolean.Input(
                    "web_tool",
                    default=True,
                    tooltip="web_search / web_fetch: look up model cards, trigger words or docs online",
                ),
                io.Combo.Input(
                    "tool_mode",
                    options=["auto", "native", "json"],
                    default="auto",
                    advanced=True,
                    tooltip="auto = native tool-calls, fall back to a JSON text protocol if the server rejects "
                            "tools. native = require tool-calling support (llama.cpp --jinja, "
                            "vLLM --enable-auto-tool-choice). json = text protocol only.",
                ),
                io.Int.Input("max_iterations", default=18, min=1, max=64, advanced=True),
            ],
            outputs=[LLMAgentConfigType.Output("agent_config")],
        )

    @classmethod
    def execute(cls, system_prompt, output_format, json_hint, assets_tool, web_tool, tool_mode,
                max_iterations) -> io.NodeOutput:
        return io.NodeOutput(
            {
                "system_prompt": system_prompt,
                "output_format": output_format,
                "json_hint": json_hint,
                "assets_tool": assets_tool,
                "web_tool": web_tool,
                "tool_mode": tool_mode,
                "max_iterations": max_iterations,
            }
        )
