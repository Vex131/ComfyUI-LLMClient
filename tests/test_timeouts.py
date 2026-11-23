import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from providers import agent, client


def load_nodes():
    def field(name, **kwargs):
        return SimpleNamespace(name=name, **kwargs)

    io = SimpleNamespace(
        ComfyNode=object, NodeOutput=lambda *values: values,
        Schema=lambda **kwargs: SimpleNamespace(**kwargs),
        Float=SimpleNamespace(Input=field), Int=SimpleNamespace(Input=field),
        String=SimpleNamespace(Input=field, Output=field),
        Image=SimpleNamespace(Input=field), Combo=SimpleNamespace(Input=field),
        Custom=lambda name: SimpleNamespace(Input=field, Output=field),
    )
    names = (
        "timeout_test", "timeout_test.nodes", "timeout_test.providers",
        "timeout_test.nodes.provider", "timeout_test.nodes.agent_config",
        "timeout_test.nodes.assets", "timeout_test.config",
        "comfy", "comfy.model_management", "comfy_api", "comfy_api.latest", "PIL",
    )
    modules = {name: ModuleType(name) for name in names}
    modules["timeout_test.providers"].client = client
    modules["timeout_test.providers"].agent = agent
    modules["timeout_test.providers"].webfetch = MagicMock()
    modules["timeout_test.providers"].websearch = MagicMock()
    modules["timeout_test.nodes.provider"].LLMProviderType = SimpleNamespace(Input=field)
    modules["timeout_test.nodes.agent_config"].LLMAgentConfigType = SimpleNamespace(Input=field)
    modules["comfy"].model_management = modules["comfy.model_management"]
    modules["comfy.model_management"].processing_interrupted = lambda: False
    modules["comfy.model_management"].throw_exception_if_processing_interrupted = lambda: None
    modules["comfy_api.latest"].io = io
    modules["comfy_api.latest"].ComfyAPISync = MagicMock()
    modules["PIL"].Image = MagicMock()
    root = Path(__file__).resolve().parents[1] / "nodes"
    with patch.dict(sys.modules, modules):
        loaded = {}
        for name in ("config", "chat", "agent"):
            spec = importlib.util.spec_from_file_location("timeout_test.nodes." + name, root / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
    return loaded


class TimeoutNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_image_input_and_vision_override(self):
        nodes = load_nodes()
        node = nodes["agent"].LLMAgent
        self.assertIn("image", [field.name for field in node.define_schema().inputs])
        model = {"base_url": "http://localhost", "model": "test"}
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        result = {"final": "ok", "reasoning": "", "messages": []}
        with patch.object(aiohttp, "ClientSession", return_value=session), \
             patch.object(client, "resolve_key", return_value="key"), \
             patch.object(agent, "run_loop", new_callable=AsyncMock, return_value=result) as run_loop, \
             patch.object(nodes["agent"], "_image_data_url", return_value="data:image/png;base64,test") as encode, \
             patch.object(nodes["agent"].config, "cached_models", return_value=[{"id": "test", "vision": False}], create=True):
            with self.assertRaisesRegex(ValueError, "does not support image"):
                await node.execute(model, "describe", image=["first"])
            with self.assertRaisesRegex(ValueError, "override"):
                await node.execute(model, "describe", image=["first"], vision="no")
            await node.execute(model, "describe", image=["first", "second"], vision="yes")
            encode.assert_called_once_with("first")
            content = run_loop.call_args.args[4][1]["content"]
            self.assertEqual(content, [
                {"type": "text", "text": "describe"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,test"}},
            ])
            await node.execute(model, "text only")
            self.assertEqual(run_loop.call_args.args[4][1]["content"], "text only")

    async def test_chat_and_agent_use_configured_and_legacy_timeouts(self):
        nodes = load_nodes()
        model = {"base_url": "http://localhost", "model": "test"}
        base = {"temperature": -1, "top_p": -1, "top_k": -1, "max_tokens": -1}
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(aiohttp, "ClientSession", return_value=session) as factory, \
             patch.object(client, "resolve_key", return_value="test-key") as resolve_key, \
             patch.object(client, "chat_stream", new_callable=AsyncMock, return_value=("ok", "")) as chat_stream, \
             patch.object(agent, "run_loop", new_callable=AsyncMock,
                          return_value={"final": "ok", "reasoning": "", "messages": []}) as run_loop:
            for config, expected in ((None, 300), (base, 300), (dict(base, timeout_seconds=900), 900)):
                with self.subTest(config=config):
                    await nodes["chat"].LLMChat.execute(model, "", "prompt", model_config=config)
                    self.assertEqual(factory.call_args.kwargs["timeout"].total, expected)
                    self.assertEqual(chat_stream.call_args.args[2], "test-key")
                    await nodes["agent"].LLMAgent.execute(model, "prompt", model_config=config)
                    self.assertEqual(factory.call_args.kwargs["timeout"].total, expected)
                    self.assertEqual(run_loop.call_args.args[2], "test-key")
            self.assertEqual(resolve_key.call_count, 6)
        self.assertNotIn("timeout_seconds", client.build_chat_payload("m", [], dict(base, timeout_seconds=900)))

    def test_config_schema_and_old_execute(self):
        node = load_nodes()["config"].LLMModelConfig
        self.assertEqual(node.define_schema().inputs[-1].default, 300)
        self.assertEqual(node.execute(-1, -1, -1, -1)[0]["timeout_seconds"], 300)
        self.assertEqual(node.execute(-1, -1, -1, -1, 900)[0]["timeout_seconds"], 900)

    async def test_streaming_total_timeout_for_chat_and_agent(self):
        async def handler(request):
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
            await asyncio.sleep(0.15)
            return response

        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        server = TestServer(app)
        await server.start_server()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=0.05)) as session:
                base = str(server.make_url(""))
                with self.assertRaises(asyncio.TimeoutError):
                    await client.chat_stream(session, base, "", {}, lambda _: None)
                with self.assertRaises(asyncio.TimeoutError):
                    await agent.chat_turn(session, base, "", {})
        finally:
            await server.close()


if __name__ == "__main__":
    unittest.main()
