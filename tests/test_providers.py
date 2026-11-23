import asyncio
import importlib
import json
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

os.environ["COMFYUI_LLMCLIENT_BACKEND"] = "file"
os.environ["COMFYUI_LLMCLIENT_FILE_DIR"] = tempfile.mkdtemp(prefix="llmclient-test-vault-")

with patch.dict(sys.modules, {"folder_paths": SimpleNamespace(get_user_directory=lambda: "test-user")}):
    import config
from providers import client, credstore


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
    async def test_probe_preserves_only_advertised_reasoning_modes(self):
        app = web.Application()

        async def models_handler(request):
            return web.json_response({"data": [
                {"id": "supported", "capabilities": {"reasoning_efforts": ["low", "medium", "high"]}},
                {"id": "unknown"},
                {"id": "invalid", "reasoning_efforts": "medium"},
            ]})

        app.router.add_get("/v1/models", models_handler)
        async def run(session, base):
            return await client.probe(session, base, "")

        result = await _with_server(app, run)
        by_id = {model["id"]: model for model in result["models"]}
        self.assertEqual(by_id["supported"]["reasoning_efforts"], ["low", "medium", "high"])
        self.assertEqual(by_id["unknown"]["reasoning_efforts"], [])
        self.assertEqual(by_id["invalid"]["reasoning_efforts"], [])

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

    async def test_probe_catchall_endpoints_stay_generic(self):
        app = web.Application()

        async def models_handler(request):
            return web.json_response({"data": [{"id": "m"}]})

        async def props_handler(request):
            return web.json_response({"error": "route not found"})

        async def sleeping_handler(request):
            return web.json_response("true")

        app.router.add_get("/v1/models", models_handler)
        app.router.add_get("/props", props_handler)
        app.router.add_get("/is_sleeping", sleeping_handler)

        async def run(session, base):
            return await client.probe(session, base, "")

        result = await _with_server(app, run)
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "generic")
        self.assertIsNone(result["context"])

    async def test_probe_vllm(self):
        app = web.Application()

        async def models_handler(request):
            return web.json_response({"data": [{"id": "m"}]})

        async def sleeping_handler(request):
            return web.json_response(False)

        app.router.add_get("/v1/models", models_handler)
        app.router.add_get("/is_sleeping", sleeping_handler)

        async def run(session, base):
            return await client.probe(session, base, "")

        result = await _with_server(app, run)
        self.assertEqual(result["profile"], "vllm")

    async def test_probe_refuses_redirects_for_credentialed_requests(self):
        app = web.Application()

        async def models_handler(request):
            raise web.HTTPFound("http://127.0.0.1:1/elsewhere")

        app.router.add_get("/v1/models", models_handler)

        async def run(session, base):
            with self.assertRaises(RuntimeError) as ctx:
                await client.probe(session, base, "secret-key")
            self.assertIn("not followed for credentialed requests", str(ctx.exception))

        await _with_server(app, run)


class ResolveKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="llmclient-vault-")
        os.environ["COMFYUI_LLMCLIENT_FILE_DIR"] = self.tmp
        importlib.reload(credstore)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_vault_key_used(self):
        credstore.set_secret("llm.https://api.example.com/v1", "vault-key")
        self.assertEqual(client.resolve_key("https://api.example.com/v1"), "vault-key")
        self.assertEqual(client.resolve_key("https://api.example.com"), "vault-key")

    def test_no_key_stored_returns_empty(self):
        self.assertEqual(client.resolve_key("https://api.example.com/v1"), "")

    def test_vault_key_on_plaintext_public_host_raises(self):
        credstore.set_secret("llm.http://api.example.com/v1", "vault-key")
        with self.assertRaises(ValueError) as ctx:
            client.resolve_key("http://api.example.com/v1")
        self.assertIn("refusing to send credentials over plaintext HTTP to a public host",
                      str(ctx.exception))

    def test_public_literal_ip_rejected_plaintext(self):
        credstore.set_secret("llm.http://203.0.113.7/v1", "vault-key")
        with self.assertRaises(ValueError) as ctx:
            client.resolve_key("http://203.0.113.7/v1")
        self.assertIn("public host", str(ctx.exception))
        credstore.set_secret("llm.https://203.0.113.7/v1", "vault-key")
        self.assertEqual(client.resolve_key("https://203.0.113.7/v1"), "vault-key")

    def test_private_and_vpn_plaintext_hosts_accept_vault_key(self):
        for url in ("http://10.1.2.3:8080/v1", "http://172.16.9.9/v1", "http://192.168.1.10/v1",
                    "http://100.84.76.75:18086/v1", "http://169.254.1.1/v1",
                    "http://[fd12::1]:8080/v1", "http://[fe80::1]:8080/v1"):
            credstore.set_secret("llm." + url, "vault-key")
            self.assertEqual(client.resolve_key(url), "vault-key", url)

    def test_transport_is_safe_matrix(self):
        for url in ("http://100.84.76.75:18086/v1", "http://10.0.0.1/v1", "http://192.168.0.5/v1",
                    "http://172.16.0.5/v1", "http://169.254.5.5/v1", "http://[fd12::1]/v1",
                    "http://[fe80::2]/v1", "http://127.0.0.1:8080/v1", "http://localhost/v1",
                    "https://203.0.113.7/v1"):
            self.assertTrue(client.transport_is_safe(url), url)
        for url in ("http://203.0.113.7/v1", "http://8.8.8.8/v1", "http://api.example.com/v1",
                    "http://[2001:db8::1]/v1"):
            self.assertFalse(client.transport_is_safe(url), url)

    def test_vault_key_allowed_on_loopback_http(self):
        credstore.set_secret("llm.http://127.0.0.1:8080/v1", "vault-key")
        self.assertEqual(client.resolve_key("http://127.0.0.1:8080/v1"), "vault-key")
        self.assertEqual(client.resolve_key("http://localhost:8080/v1"), "")


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

    async def test_chat_stream_refuses_redirects_for_credentialed_requests(self):
        app = web.Application()

        async def chat_handler(request):
            raise web.HTTPFound("http://127.0.0.1:1/elsewhere")

        app.router.add_post("/v1/chat/completions", chat_handler)

        async def run(session, base):
            with self.assertRaises(RuntimeError) as ctx:
                await client.chat_stream(
                    session, base, "secret-key",
                    {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                    lambda n: None,
                )
            self.assertIn("not followed for credentialed requests", str(ctx.exception))

        await _with_server(app, run)

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

    async def test_unload_sleep_post_refuses_redirects(self):
        app = web.Application()

        async def sleep_handler(request):
            raise web.HTTPFound("http://127.0.0.1:1/elsewhere")

        app.router.add_post("/sleep", sleep_handler)

        async def run(session, base):
            with self.assertRaises(RuntimeError) as ctx:
                await client.unload_command(session, base, "secret-key", "vllm", "offload_vram_to_ram", "m")
            self.assertIn("not followed for credentialed requests", str(ctx.exception))

        await _with_server(app, run)

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

    def test_config_without_reasoning_effort_key(self):
        sampling = {"temperature": -1.0, "top_p": -1.0, "top_k": -1, "max_tokens": -1}
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
    def test_cache_directory_override_and_default(self):
        path = Path(config.__file__)
        spec = importlib.util.spec_from_file_location("cache_config_test", path)
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "new", "cache")
            with patch.dict(os.environ, {"COMFYUI_LLMCLIENT_CACHE_PATH": target}), \
                 patch.dict(sys.modules, {"folder_paths": config.folder_paths}):
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertEqual(module.CACHE_PATH, os.path.join(target, "providers.json"))
                module.save_cache({"test": 1})
                self.assertEqual(module.load_cache(), {"test": 1})
            with patch.dict(os.environ, {}, clear=True):
                with patch.dict(sys.modules, {"folder_paths": SimpleNamespace(get_user_directory=lambda: tmp)}):
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                self.assertEqual(module.CACHE_PATH, os.path.join(tmp, "llmclient", "providers.json"))
                module.save_cache({"test": 2})
                self.assertEqual(module.load_cache(), {"test": 2})

    def test_legacy_cache_is_read_until_new_cache_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "legacy.json")
            new = os.path.join(tmp, "user", "providers.json")
            with open(legacy, "w") as f:
                json.dump({"old": {"models": []}}, f)
            with patch.object(config, "CACHE_PATH", new), \
                 patch.object(config, "LEGACY_CACHE_PATH", legacy), \
                 patch.object(config, "CACHE_OVERRIDE", None):
                self.assertIn("old", config.load_cache())
                self.assertTrue(os.path.isfile(new))
                self.assertTrue(os.path.isfile(legacy))
                config.update_cache("new", "generic", [])
                self.assertEqual(set(config.load_cache()), {"old", "new"})
                config.save_cache({})
                self.assertEqual(config.load_cache(), {})

    def test_legacy_migration_failure_preserves_read_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "legacy.json")
            with open(legacy, "w") as f:
                json.dump({"old": {"models": []}}, f)
            with patch.object(config, "CACHE_PATH", os.path.join(tmp, "new", "providers.json")), \
                 patch.object(config, "LEGACY_CACHE_PATH", legacy), \
                 patch.object(config, "CACHE_OVERRIDE", None), \
                 patch.object(config.os, "makedirs", side_effect=PermissionError("read-only")), \
                 self.assertLogs(config.__name__, level="WARNING"):
                self.assertIn("old", config.load_cache())
            with patch.object(config, "CACHE_PATH", os.path.join(tmp, "override", "providers.json")), \
                 patch.object(config, "LEGACY_CACHE_PATH", legacy), \
                 patch.object(config, "CACHE_OVERRIDE", "override"):
                self.assertEqual(config.load_cache(), {})

    def test_cache_write_error_is_not_silenced(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocked = os.path.join(tmp, "file")
            with open(blocked, "w") as f:
                f.write("not a directory")
            with patch.object(config, "CACHE_PATH", os.path.join(blocked, "providers.json")):
                self.assertEqual(config.load_cache(), {})
                with self.assertRaises(OSError):
                    config.save_cache({})

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
