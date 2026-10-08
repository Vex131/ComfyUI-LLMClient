import asyncio
import json
import os
import tempfile
import unittest

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

import config
from providers import client, profiles


class FormatInfoTests(unittest.TestCase):
    def test_format_info(self):
        entry = {
            "profile": "llamacpp",
            "context": 8192,
            "models": [
                {"id": "vl-model", "vision": True, "reasoning": None},
                {"id": "text-model", "vision": False, "reasoning": None},
                {"id": "unknown-model", "vision": None, "reasoning": None},
            ],
        }
        info = profiles.format_info(entry)
        self.assertIn("Profile: llamacpp", info)
        self.assertIn("Context: 8192 tokens", info)
        self.assertIn("Unload: llama.cpp router load/unload", info)
        self.assertIn("vl-model [vision]", info)
        self.assertIn("text-model [text-only]", info)
        self.assertIn("  - unknown-model\n", info + "\n")

    def test_format_info_no_unload(self):
        info = profiles.format_info({"profile": "openai", "models": [{"id": "gpt", "vision": None}]})
        self.assertIn("Unload: not supported", info)
        self.assertNotIn("Context:", info)


async def _with_server(app, coro):
    server = TestServer(app)
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            return await coro(session, str(server.make_url("")))
    finally:
        await server.close()


def llamacpp_app():
    app = web.Application()
    models = {
        "data": [
            {"id": "qwen2.5-vl", "capabilities": {"multimodal": True}},
            {"id": "qwen2.5", "capabilities": {"multimodal": False}},
        ]
    }

    async def models_handler(request):
        return web.json_response(models)

    async def props_handler(request):
        return web.json_response({"status": "ok", "slot_n_ctx": 4096})

    app.router.add_get("/v1/models", models_handler)
    app.router.add_get("/props", props_handler)
    return app


def generic_app():
    app = web.Application()
    models = {"data": [{"id": "some-model"}, {"id": "id", "created": 1}]}

    async def models_handler(request):
        return web.json_response(models)

    app.router.add_get("/v1/models", models_handler)
    return app


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_llamacpp(self):
        async def run(session, base):
            return await client.probe(session, base, "")

        result = await _with_server(llamacpp_app(), run)
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "llamacpp")
        self.assertEqual(result["context"], 4096)
        by_id = {m["id"]: m for m in result["models"]}
        self.assertIs(by_id["qwen2.5-vl"]["vision"], True)
        self.assertIs(by_id["qwen2.5"]["vision"], False)

    async def test_probe_generic(self):
        async def run(session, base):
            return await client.probe(session, base, "secret-key")

        result = await _with_server(generic_app(), run)
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "generic")
        by_id = {m["id"]: m for m in result["models"]}
        self.assertIsNone(by_id["some-model"]["vision"])
        self.assertNotIn("secret-key", json.dumps(result))


class ChatStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_stream_sse(self):
        async def chat_handler(request):
            body = await request.json()
            self.assertIs(body["stream"], True)
            resp = web.StreamResponse()
            resp.content_type = "text/event-stream"
            await resp.prepare(request)
            chunks = [
                {"choices": [{"delta": {"content": "Hel"}}]},
                {"choices": [{"delta": {"reasoning_content": "thinking"}}]},
                {"choices": [{"delta": {"content": "lo"}}]},
            ]
            for chunk in chunks:
                await resp.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp

        app = web.Application()
        app.router.add_post("/v1/chat/completions", chat_handler)
        chunks_seen = []

        async def run(session, base):
            return await client.chat_stream(
                session, base, "",
                {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                lambda n: chunks_seen.append(n),
            )

        text, reasoning = await _with_server(app, run)
        self.assertEqual(text, "Hello")
        self.assertEqual(reasoning, "thinking")
        self.assertEqual(chunks_seen, [3, 3, 5])

    async def test_chat_stream_interrupt_stops_generation(self):
        streamed = []
        disconnected = []

        async def chat_handler(request):
            resp = web.StreamResponse()
            resp.content_type = "text/event-stream"
            await resp.prepare(request)
            try:
                for i in range(100):
                    await asyncio.sleep(0.01)
                    chunk = {"choices": [{"delta": {"content": "x{} ".format(i)}}]}
                    await resp.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    streamed.append(i)
                await resp.write(b"data: [DONE]\n\n")
            except (ConnectionResetError, asyncio.CancelledError):
                disconnected.append(True)
            return resp

        app = web.Application()
        app.router.add_post("/v1/chat/completions", chat_handler)
        seen = []

        async def run(session, base):
            return await client.chat_stream(
                session, base, "",
                {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                lambda n: seen.append(n),
                should_interrupt=lambda: len(seen) >= 3,
            )

        text, _ = await _with_server(app, run)
        self.assertEqual(text.count("x"), 3)
        self.assertLess(len(streamed), 100)
        self.assertTrue(disconnected)

    async def test_default_config_sentinels_never_reach_provider(self):
        bodies = []

        async def chat_handler(request):
            bodies.append(await request.json())
            return web.json_response({"choices": [{"delta": {"content": "ok"}}]})

        app = web.Application()
        app.router.add_post("/v1/chat/completions", chat_handler)

        sampling = {"temperature": -1.0, "top_p": -1.0, "top_k": -1, "max_tokens": -1,
                    "reasoning_effort": ""}
        payload = client.build_chat_payload("m", [{"role": "user", "content": "hi"}], sampling, seed=-1)

        async def run(session, base):
            return await client.chat_stream(session, base, "", payload, lambda n: None)

        await _with_server(app, run)
        self.assertEqual(bodies[0], {"model": "m", "messages": [{"role": "user", "content": "hi"}],
                                    "stream": True})


class UnloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_unload_vllm_sleep(self):
        calls = []

        async def sleep_handler(request):
            calls.append((request.path, request.query.get("level")))
            return web.json_response({"status": "ok"})

        app = web.Application()
        app.router.add_post("/sleep", sleep_handler)

        async def run(session, base):
            return await client.unload_command(session, base, "", "vllm", "offload_vram_to_ram", "m")

        status = await _with_server(app, run)
        self.assertEqual(calls, [("/sleep", "1")])
        self.assertIn("offloaded", status)

    async def test_unload_unsupported_profile(self):
        with self.assertRaises(ValueError) as ctx:
            await client.unload_command(None, "http://127.0.0.1:1/v1", "", "openai", "release_all", "m")
        self.assertIn("does not expose an unload API", str(ctx.exception))


class BuildPayloadTests(unittest.TestCase):
    MESSAGES = [{"role": "user", "content": "hi"}]

    def test_no_config_provider_defaults(self):
        payload = client.build_chat_payload("m", self.MESSAGES)
        self.assertEqual(payload, {"model": "m", "messages": self.MESSAGES})

    def test_config_sentinels_skipped(self):
        sampling = {"temperature": -1.0, "top_p": -1.0, "top_k": -1, "max_tokens": -1,
                    "reasoning_effort": ""}
        payload = client.build_chat_payload("m", self.MESSAGES, sampling, seed=-1)
        self.assertEqual(payload, {"model": "m", "messages": self.MESSAGES})

    def test_config_values_and_seed(self):
        sampling = {"temperature": 0.7, "top_p": 0.9, "top_k": 40, "max_tokens": 512,
                     "reasoning_effort": "xhigh"}
        payload = client.build_chat_payload("m", self.MESSAGES, sampling, seed=7)
        self.assertEqual(payload["temperature"], 0.7)
        self.assertEqual(payload["top_p"], 0.9)
        self.assertEqual(payload["top_k"], 40)
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["reasoning_effort"], "xhigh")
        self.assertEqual(payload["seed"], 7)


class ConfigTests(unittest.TestCase):
    def test_cache_round_trip(self):
        original = config.CACHE_PATH
        with tempfile.TemporaryDirectory() as tmp:
            config.CACHE_PATH = os.path.join(tmp, "providers.json")
            try:
                self.assertEqual(config.cached_models("http://a/v1"), [])
                self.assertIsNone(config.cached_entry("http://a/v1"))
                config.update_cache("http://a/v1", "llamacpp",
                                    [{"id": "shared", "vision": True, "reasoning": None}], context=4096)
                config.update_cache("http://b/v1", "vllm",
                                    [{"id": "shared", "vision": None, "reasoning": None},
                                     {"id": "other", "vision": None, "reasoning": None}])
                self.assertEqual(config.cached_models("http://a/v1")[0]["id"], "shared")
                self.assertEqual(config.cached_entry("http://a/v1")["context"], 4096)
                self.assertEqual(config.all_cached_model_ids(), ["shared", "other"])
                with open(config.CACHE_PATH, "w") as f:
                    f.write("{not json")
                self.assertEqual(config.load_cache(), {})
            finally:
                config.CACHE_PATH = original


if __name__ == "__main__":
    unittest.main()
