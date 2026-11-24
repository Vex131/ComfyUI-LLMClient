from comfy_api.latest import io

LLMContextManagementType = io.Custom("LLM_CONTEXT_MANAGEMENT")


class LLMContextManagement(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="LLMContextManagement", display_name="LLM Context Management",
            category="LLM Client",
            inputs=[
                io.Boolean.Input("enabled", default=False),
                io.Combo.Input("strategy", options=["compaction"], default="compaction"),
                io.String.Input("buffer", default="15%",
                                tooltip="Below 100 means percent, optionally with %; above 100 means tokens."),
                io.Int.Input("keep_recent_tokens", default=2048, min=0, max=1000000),
            ],
            outputs=[LLMContextManagementType.Output("context_management")],
        )

    @classmethod
    def execute(cls, enabled, strategy, buffer, keep_recent_tokens):
        return io.NodeOutput({"enabled": enabled, "strategy": strategy, "buffer": buffer,
                              "keep_recent_tokens": keep_recent_tokens})
