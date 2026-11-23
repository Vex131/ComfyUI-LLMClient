import asyncio
import json
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from providers import agent, client, memory, rag


def load_nodes():
    def field(name, **kwargs):
        return SimpleNamespace(name=name, **kwargs)

    io = SimpleNamespace(
        ComfyNode=object, NodeOutput=lambda *values: values,
        Schema=lambda **kwargs: SimpleNamespace(**kwargs),
        Float=SimpleNamespace(Input=field), Int=SimpleNamespace(Input=field),
        String=SimpleNamespace(Input=field, Output=field),
        Boolean=SimpleNamespace(Input=field),
        Image=SimpleNamespace(Input=field), Combo=SimpleNamespace(Input=field),
        Custom=lambda name: SimpleNamespace(Input=field, Output=field),
        Hidden=SimpleNamespace(unique_id="UNIQUE_ID"),
    )
    names = (
        "timeout_test", "timeout_test.nodes", "timeout_test.providers",
        "timeout_test.nodes.provider", "timeout_test.nodes.agent_config",
        "timeout_test.nodes.rag",
        "timeout_test.nodes.assets", "timeout_test.config",
        "comfy", "comfy.model_management", "comfy_api", "comfy_api.latest", "PIL",
    )
    modules = {name: ModuleType(name) for name in names}
    modules["timeout_test.providers"].client = client
    modules["timeout_test.providers"].agent = agent
    modules["timeout_test.providers"].rag = rag
    modules["timeout_test.providers"].memory = memory
    modules["timeout_test.providers"].webfetch = MagicMock()
    modules["timeout_test.providers"].websearch = MagicMock()
    modules["timeout_test.nodes.provider"].LLMProviderType = SimpleNamespace(Input=field)
    modules["timeout_test.nodes.agent_config"].LLMAgentConfigType = SimpleNamespace(Input=field)
    modules["timeout_test.nodes.rag"].LLMDocumentsType = SimpleNamespace(Input=field, Output=field)
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
    async def test_blank_reasoning_effort_defaults_to_medium(self):
        nodes = load_nodes()
        model = {"base_url": "http://localhost", "model": "test"}
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(aiohttp, "ClientSession", return_value=session), \
             patch.object(client, "resolve_key", return_value="key"), \
             patch.object(client, "chat_stream", new_callable=AsyncMock, return_value=("ok", "")) as chat, \
             patch.object(agent, "run_loop", new_callable=AsyncMock,
                          return_value={"final": "ok", "reasoning": "", "messages": []}) as loop:
            await nodes["chat"].LLMChat.execute(model, "", "hello", reasoning_effort="  ")
            self.assertEqual(chat.call_args.args[3]["reasoning_effort"], "medium")
            await nodes["agent"].LLMAgent.execute(model, "hello", reasoning_effort="")
            self.assertEqual(loop.call_args.kwargs["sampling"]["reasoning_effort"], "medium")

    async def test_documents_input_enables_search_tool(self):
        nodes = load_nodes()
        node = nodes["agent"].LLMAgent
        model = {"base_url": "http://localhost", "model": "test"}
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        index = MagicMock()
        index.search.return_value = "source passage"
        result = {"final": "answer", "reasoning": "", "messages": []}
        with patch.object(aiohttp, "ClientSession", return_value=session), \
             patch.object(client, "resolve_key", return_value="key"), \
             patch.object(rag, "load_index", return_value=index) as build, \
             patch.object(agent, "run_loop", new_callable=AsyncMock, return_value=result) as loop:
            await node.execute(model, "question", documents={"document_path": "docs"},
                               agent_config={"rag_config": {"embedding_model_path": "model", "top_k": 3}})
            build.assert_called_once_with("docs", "model")
            tool = loop.call_args.kwargs["tool_impls"]["search_documents"]
            self.assertEqual(await tool({"query": "search"}, session), "source passage")
            index.search.assert_called_once_with("search", 3)

    async def test_documents_require_rag_config(self):
        nodes = load_nodes()
        with patch.object(client, "resolve_key", return_value="key"):
            with self.assertRaisesRegex(ValueError, "LLM RAG Config"):
                await nodes["agent"].LLMAgent.execute(
                    {"base_url": "http://localhost", "model": "test"},
                    "question", documents={"document_path": "docs"})

    async def test_chat_and_agent_memory_conversations(self):
        nodes = load_nodes()
        chat = nodes["chat"].LLMChat
        agent_node = nodes["agent"].LLMAgent
        chat.hidden = SimpleNamespace(unique_id="1")
        agent_node.hidden = SimpleNamespace(unique_id="2")
        model = {"base_url": "http://localhost", "model": "test"}
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        chat_session, agent_session = memory.new_session(), memory.new_session()

        async def fake_loop(*args, **kwargs):
            messages = list(args[4]) + [{"role": "assistant", "content": "agent reply"}]
            return {"final": "agent reply", "reasoning": "", "messages": messages}

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(memory, "session_directory", return_value=Path(directory)), \
             patch.object(aiohttp, "ClientSession", return_value=session), \
             patch.object(client, "resolve_key", return_value="key"), \
             patch.object(client, "chat_stream", new_callable=AsyncMock, return_value=("reply", "")) as stream, \
             patch.object(agent, "run_loop", new_callable=AsyncMock, side_effect=fake_loop) as loop:
            await chat.execute(model, "", "first", memory=True, sessions=chat_session)
            await chat.execute(model, "", "second", memory=True, sessions=chat_session)
            messages = stream.call_args.args[3]["messages"]
            self.assertEqual([m["content"] for m in messages], ["first", "reply", "second"])
            await chat.execute(model, "", "third", memory=True, sessions=memory.new_session())
            self.assertEqual(len(stream.call_args.args[3]["messages"]), 1)
            await agent_node.execute(model, "first", memory=True, sessions=agent_session)
            await agent_node.execute(model, "second", memory=True, sessions=agent_session)
            messages = loop.call_args.args[4]
            self.assertEqual([m["content"] for m in messages[1:]], ["first", "agent reply", "second"])

    async def test_agent_persists_tool_calls_and_results(self):
        nodes = load_nodes()
        model = {"base_url": "http://localhost", "model": "test"}
        session_id = memory.new_session()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        seen = []

        async def fake_loop(*args, **kwargs):
            messages = list(args[4])
            seen.append(messages)
            if len(seen) == 1:
                messages.extend([
                    {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "c", "type": "function", "function": {"name": "search", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "c", "content": "source result"},
                    {"role": "assistant", "content": "answer"},
                ])
            return {"final": "answer", "reasoning": "thinking", "messages": messages}

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(memory, "session_directory", return_value=Path(directory)), \
             patch.object(aiohttp, "ClientSession", return_value=session), \
             patch.object(client, "resolve_key", return_value="key"), \
             patch.object(agent, "run_loop", new_callable=AsyncMock, side_effect=fake_loop):
            await nodes["agent"].LLMAgent.execute(model, "first", memory=True, sessions=session_id)
            await nodes["agent"].LLMAgent.execute(model, "second", memory=True, sessions=session_id)
            self.assertIn("source result", [m.get("content") for m in seen[1]])
            saved = json.loads(memory._session_path("agent", session_id).read_text(encoding="utf-8"))
            self.assertEqual(saved["runs"][0]["reasoning"], "thinking")
            self.assertTrue(any(m.get("tool_calls") for m in saved["messages"]))

    async def test_agent_image_input_and_vision_override(self):
        nodes = load_nodes()
        node = nodes["agent"].LLMAgent
        with patch.object(rag, "model_options", return_value=[rag.DIRECT_MODEL]):
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
        self.assertEqual(next(field.default for field in node.define_schema().inputs
                              if field.name == "timeout_seconds"), 300)
        self.assertEqual(node.execute(-1, -1, -1, -1)[0]["timeout_seconds"], 300)
        self.assertEqual(node.execute(-1, -1, -1, -1, 900)[0]["timeout_seconds"], 900)
        configured = node.execute(-1, -1, -1, -1, 900, 8192)[0]
        self.assertEqual(configured["max_context"], 8192)
        self.assertNotIn("max_context", client.build_chat_payload("m", [], configured))

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
