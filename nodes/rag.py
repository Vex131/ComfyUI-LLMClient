from comfy_api.latest import io

from ..providers import rag

LLMDocumentsType = io.Custom("LLM_DOCUMENTS")
LLMRagConfigType = io.Custom("LLM_RAG_CONFIG")


class LoadDocuments(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="LLMLoadDocuments",
            display_name="Load Documents",
            category="LLM Client",
            inputs=[
                io.String.Input("document_path", default="", tooltip="File or folder on the ComfyUI server; use the upload buttons for files on your device."),
            ],
            outputs=[LLMDocumentsType.Output("documents", tooltip="Connect to LLM Agent to enable document search.")],
        )

    @classmethod
    def fingerprint_inputs(cls, document_path=None, **kwargs):
        if document_path is None:
            return float("nan")
        return rag.document_fingerprint(document_path)

    @classmethod
    def execute(cls, document_path):
        return io.NodeOutput({"document_path": document_path})


class LLMRagConfig(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        options = [io.DynamicCombo.Option(rag.DIRECT_MODEL, [
            io.String.Input("embedding_model_path", default="",
                            tooltip="Directory containing a local Sentence Transformers model."),
        ])]
        options.extend(io.DynamicCombo.Option(name, []) for name in rag.model_options()
                       if name != rag.DIRECT_MODEL)
        return io.Schema(
            node_id="LLMRagConfig",
            display_name="LLM RAG Config",
            category="LLM Client",
            inputs=[
                io.DynamicCombo.Input("embedding_model", options=options,
                                      tooltip="Preset sizes are approximate download sizes."),
                io.Int.Input("top_k", default=5, min=1, max=20, advanced=True,
                             tooltip="Maximum document passages returned for each search."),
            ],
            outputs=[LLMRagConfigType.Output("rag_config", tooltip="Connect to LLM Agent Config to configure document search.")],
        )

    @classmethod
    def fingerprint_inputs(cls, embedding_model=None, **kwargs):
        if not isinstance(embedding_model, dict):
            return float("nan")
        try:
            path = rag.resolve_model(embedding_model["embedding_model"],
                                     embedding_model.get("embedding_model_path", ""))
            return rag.model_fingerprint(path)
        except (KeyError, OSError, ValueError):
            return float("nan")

    @classmethod
    def execute(cls, embedding_model, top_k=5):
        model_path = rag.resolve_model(embedding_model["embedding_model"],
                                       embedding_model.get("embedding_model_path", ""))
        return io.NodeOutput({"embedding_model_path": model_path, "top_k": top_k})
