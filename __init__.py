from typing_extensions import override

from comfy_api.latest import ComfyExtension, io

from .nodes.agent import LLMAgent
from .nodes.agent_config import LLMAgentConfig
from .nodes.chat import LLMChat
from .nodes.config import LLMModelConfig
from .nodes.extract import LLMPatternExtract
from .nodes.free_models import ComfyFreeModels
from .nodes.provider import LLMProvider
from .nodes.rag import LoadDocuments, LLMRagConfig
from .nodes.unload import LLMUnload

WEB_DIRECTORY = "web"


class LLMClientExtension(ComfyExtension):
    @override
    async def on_load(self) -> None:
        from . import routes

    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            LLMProvider,
            LLMModelConfig,
            LLMChat,
            LLMAgentConfig,
            LoadDocuments,
            LLMRagConfig,
            LLMAgent,
            LLMPatternExtract,
            LLMUnload,
            ComfyFreeModels,
        ]


async def comfy_entrypoint() -> LLMClientExtension:
    return LLMClientExtension()
