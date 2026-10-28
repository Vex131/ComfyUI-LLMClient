import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


def load_unload():
    """Load the node with a minimal Comfy API stand-in, without ComfyUI."""
    def field(name, **kwargs):
        return SimpleNamespace(name=name, **kwargs)

    io = SimpleNamespace(
        ComfyNode=object,
        Schema=lambda **kwargs: SimpleNamespace(**kwargs),
        String=SimpleNamespace(Input=field, Output=field),
        Combo=SimpleNamespace(Input=field),
        NodeOutput=lambda *values: values,
    )
    modules = {name: ModuleType(name) for name in (
        "unload_test_package", "unload_test_package.nodes",
        "unload_test_package.config", "unload_test_package.providers",
        "unload_test_package.providers.client", "unload_test_package.nodes.provider",
        "comfy_api", "comfy_api.latest",
    )}
    modules["comfy_api.latest"].io = io
    modules["unload_test_package.nodes.provider"].LLMProviderType = SimpleNamespace(Input=field)
    config = modules["unload_test_package.config"]
    config.cached_entry = MagicMock(return_value={"profile": "vllm"})
    client = modules["unload_test_package.providers.client"]
    client.unload_command = AsyncMock(return_value="unloaded")
    spec = importlib.util.spec_from_file_location(
        "unload_test_package.nodes.unload",
        Path(__file__).resolve().parents[1] / "nodes" / "unload.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module, config, client


class UnloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module, self.config, self.client = load_unload()
        self.model = {"base_url": "http://localhost:8000/v1", "model": "test"}
        self.session = MagicMock()
        self.session.__aenter__ = AsyncMock(return_value=self.session)
        self.session.__aexit__ = AsyncMock(return_value=False)
        self.session_patch = patch.object(
            self.module.aiohttp, "ClientSession", return_value=self.session
        )
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)

    def test_schema_preserves_existing_sockets(self):
        schema = self.module.LLMUnload.define_schema()
        self.assertEqual([item.name for item in schema.inputs], ["model", "mode", "text"])
        text = schema.inputs[-1]
        self.assertTrue(text.optional)
        self.assertTrue(text.force_input)
        self.assertEqual([item.name for item in schema.outputs], ["status", "text"])

    async def test_text_returned_only_after_unload_completes(self):
        text = "  generated text\n\u2603\n"
        started = asyncio.Event()
        finish = asyncio.Event()

        async def unload(*args):
            self.assertEqual(args[4], "release_all")
            started.set()
            await finish.wait()
            return "finished"

        self.client.unload_command.side_effect = unload
        task = asyncio.create_task(self.module.LLMUnload.execute(self.model, "release_all", text))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            self.assertFalse(task.done())
        finally:
            finish.set()
        result = await asyncio.wait_for(task, timeout=2)
        self.assertEqual(result, ("finished", text))
        self.client.unload_command.assert_awaited_once()

    async def test_old_calls_and_wake_up_need_no_text(self):
        for mode in ("offload_vram_to_ram", "wake_up"):
            with self.subTest(mode=mode):
                result = await self.module.LLMUnload.execute(self.model, mode)
                self.assertEqual(result, ("unloaded", ""))

    async def test_failure_does_not_return_passthrough(self):
        self.client.unload_command.side_effect = RuntimeError("unload failed")
        with self.assertRaisesRegex(RuntimeError, "unload failed"):
            await self.module.LLMUnload.execute(self.model, "release_all", "text")


if __name__ == "__main__":
    unittest.main()
