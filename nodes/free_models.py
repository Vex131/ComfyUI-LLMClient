import gc

import comfy.model_management as model_management
from comfy_api.latest import io


class ComfyFreeModels(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        data_type = io.MatchType.Template("data")
        return io.Schema(
            node_id="LLMFreeComfyModels",
            display_name="Free ComfyUI Models",
            category="LLM Client",
            inputs=[
                io.MatchType.Input("anything", template=data_type,
                                   tooltip="Data to pass through after releasing ComfyUI models."),
                io.Combo.Input("mode", options=["free_vram", "free_vram_and_ram"],
                               default="free_vram",
                               tooltip="RAM cleanup collects unused objects; active workflow references can keep model weights in RAM."),
            ],
            outputs=[
                io.MatchType.Output(template=data_type, display_name="anything",
                                    tooltip="The input data, unchanged."),
                io.String.Output("status", tooltip="Result of the ComfyUI model unload request."),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    def execute(cls, anything, mode="free_vram"):
        if mode == "free_vram_and_ram_experimental":
            mode = "free_vram_and_ram"
        if mode not in ("free_vram", "free_vram_and_ram"):
            raise ValueError("Unknown cleanup mode: {}".format(mode))
        model_management.unload_all_models()
        if mode == "free_vram_and_ram":
            gc.collect()
        model_management.soft_empty_cache()
        if mode == "free_vram_and_ram":
            status = ("ComfyUI model unload and RAM cleanup completed. Referenced model weights "
                      "may remain in RAM; external providers were not affected.")
        else:
            status = "ComfyUI model unload completed; external providers were not affected."
        return io.NodeOutput(anything, status)
