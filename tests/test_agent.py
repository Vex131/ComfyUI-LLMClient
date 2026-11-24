import asyncio
import json
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from providers import agent, webfetch


async def _with_server(app, coro):
    server = TestServer(app)
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            return await coro(session, str(server.make_url("")))
    finally:
        await server.close()


def sse_body(chunks):
    body = "".join("data: {}\n\n".format(json.dumps(c)) for c in chunks)
    return body + "data: [DONE]\n\n"


def delta_response(chunks):
    return web.Response(text=sse_body([{"choices": [{"delta": d}]} for d in chunks]))


def tool_call_chunks(name, arguments, call_id="call_1", content="working"):
    return [
        {"content": content},
        {"tool_calls": [{"index": 0, "id": call_id, "function": {"name": name, "arguments": ""}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": arguments}}]},
    ]


class ChatTurnTests(unittest.TestCase):
    def test_stream_usage_recorded_for_agent(self):
        from providers.stats import RunStats

        async def handler(request):
            body = await request.json()
            self.assertEqual(body["stream_options"], {"include_usage": True})
            return web.Response(text=sse_body([
                {"choices": [{"delta": {"content": "ok"}}]},
                {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                                           "prompt_tokens_details": {"cached_tokens": 80},
                                           "completion_tokens_details": {"reasoning_tokens": 3}}},
            ]))

        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        stats = RunStats(200)

        async def run(session, base):
            return await agent.chat_turn(session, base, "", {"model": "m", "messages": []}, stats=stats)

        self.assertEqual(asyncio.run(_with_server(app, run))[0], "ok")
        self.assertIn("Cache Read: 80", stats.render())
        self.assertIn("Reasoning:  3", stats.render())

    def test_content_and_split_tool_calls(self):
        async def handler(request):
            return delta_response([
                {"content": "Hel"},
                {"content": "lo", "reasoning": "th"},
                {"tool_calls": [{"index": 1, "id": "c2", "function": {"name": "web_fetch"}}]},
                {"tool_calls": [{"index": 1, "function": {"arguments": '{"url": '}}]},
                {"tool_calls": [{"index": 1, "function": {"arguments": '"http://x"}'}}]},
            ])

        async def run(session, base):
            return await agent.chat_turn(session, base, "", {"model": "m", "messages": []})

        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        text, reasoning, calls = asyncio.run(_with_server(app, run))
        self.assertEqual(text, "Hello")
        self.assertEqual(reasoning, "th")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], "c2")
        self.assertEqual(calls[0]["name"], "web_fetch")
        self.assertEqual(json.loads(calls[0]["arguments"]), {"url": "http://x"})

    def test_http_error(self):
        async def handler(request):
            return web.Response(status=400, text="bad request")

        async def run(session, base):
            return await agent.chat_turn(session, base, "", {"model": "m", "messages": []})

        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        with self.assertRaises(RuntimeError):
            asyncio.run(_with_server(app, run))

    def test_redirect_not_followed_for_credentialed_request(self):
        hits = []

        async def redirect_handler(request):
            hits.append("main")
            return web.Response(status=302, headers={"Location": "/followed"})

        async def followed_handler(request):
            hits.append("followed")
            return delta_response([{"content": "leaked"}])

        async def run(session, base):
            return await agent.chat_turn(session, base, "sk-secret", {"model": "m", "messages": []})

        app = web.Application()
        app.router.add_post("/v1/chat/completions", redirect_handler)
        app.router.add_post("/followed", followed_handler)
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(_with_server(app, run))
        self.assertIn("302", str(ctx.exception))
        self.assertIn("redirect", str(ctx.exception))
        self.assertNotIn("tool", str(ctx.exception).lower())
        self.assertEqual(hits, ["main"])


class ParseReactTests(unittest.TestCase):
    def test_fenced(self):
        obj = agent.parse_react('```json\n{"final": "ok"}\n```')
        self.assertEqual(obj, {"final": "ok"})

    def test_bare_with_prose(self):
        obj = agent.parse_react('Let me check. {"tool": "web_fetch", "args": {"url": "http://x"}} thanks')
        self.assertEqual(obj["tool"], "web_fetch")
        self.assertEqual(obj["args"], {"url": "http://x"})

    def test_nested_and_braces_in_string(self):
        obj = agent.parse_react('{"tool": "t", "args": {"a": {"b": 1}, "s": "a{b}c"}}')
        self.assertEqual(obj["args"], {"a": {"b": 1}, "s": "a{b}c"})

    def test_no_json(self):
        self.assertIsNone(agent.parse_react("plain text answer"))

    def test_plain_dict_without_protocol_keys_ignored(self):
        self.assertIsNone(agent.parse_react('{"foo": 1}'))


class RunLoopTests(unittest.TestCase):
    def _tool_app(self, scripted):
        state = {"turn": 0}

        async def handler(request):
            body = await request.json()
            turn = state["turn"]
            state["turn"] += 1
            return scripted(body, turn)

        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        return app

    def test_native_two_turns(self):
        def scripted(body, turn):
            if "tools" not in body:
                return web.Response(status=500, text="tools missing")
            if turn == 0:
                return delta_response(tool_call_chunks("list_local_models", '{"category": "loras"}'))
            return delta_response([{"content": "final answer"}])

        seen = []

        async def list_impl(args, session):
            seen.append(args)
            return "2 loras"

        async def run(session, base):
            messages = [{"role": "user", "content": "list loras"}]
            return await agent.run_loop(session, base, "", "m", messages,
                                        tool_impls={"list_local_models": list_impl},
                                        max_iterations=4)

        result = asyncio.run(_with_server(self._tool_app(scripted), run))
        self.assertFalse(result["react"])
        self.assertEqual(result["final"], "final answer")
        self.assertEqual(result["iterations"], 2)
        self.assertEqual(seen, [{"category": "loras"}])
        tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["content"], "2 loras")
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_1")

    def test_replays_per_turn_reasoning_with_tool_history(self):
        bodies = []

        def scripted(body, turn):
            bodies.append(body)
            if turn == 0:
                return delta_response([{"reasoning_content": "first thought"}]
                                      + tool_call_chunks("list_local_models", "{}"))
            return delta_response([{"reasoning": "final thought"}, {"content": "done"}])

        async def run(session, base):
            return await agent.run_loop(session, base, "", "m", [{"role": "user", "content": "list"}],
                                        tool_impls={"list_local_models": lambda *_: "result"})

        result = asyncio.run(_with_server(self._tool_app(scripted), run))
        assistant = bodies[1]["messages"][1]
        self.assertEqual(assistant["reasoning_content"], "first thought")
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_1")
        self.assertEqual(bodies[1]["messages"][2]["role"], "tool")
        self.assertEqual(result["messages"][-1]["reasoning_content"], "final thought")

    def test_auto_falls_back_to_react(self):
        def scripted(body, turn):
            if "tools" in body:
                return web.Response(status=400, text="automatic tool choice not supported by this model")
            if turn == 1:
                return delta_response([{"content": '{"tool": "echo", "args": {"x": 1}}'}])
            return delta_response([{"content": '{"final": "done"}'}])

        async def echo_impl(args, session):
            return "echoed {}".format(args.get("x"))

        async def run(session, base):
            messages = [{"role": "user", "content": "hi"}]
            return await agent.run_loop(session, base, "", "m", messages,
                                        tool_impls={"echo": echo_impl},
                                        max_iterations=4)

        result = asyncio.run(_with_server(self._tool_app(scripted), run))
        self.assertTrue(result["react"])
        self.assertEqual(result["final"], "done")
        joined = json.dumps(result["messages"])
        self.assertIn("TOOL_RESULT(echo): echoed 1", joined)

    def test_react_mode_direct(self):
        def scripted(body, turn):
            if "tools" in body:
                return web.Response(status=500, text="tools should not be sent")
            return delta_response([{"content": '{"final": "hi"}'}])

        async def run(session, base):
            messages = [{"role": "user", "content": "hi"}]
            return await agent.run_loop(session, base, "", "m", messages,
                                        tool_impls={"web_fetch": None},
                                        tool_mode="json")

        app = self._tool_app(scripted)
        result = asyncio.run(_with_server(app, run))
        self.assertTrue(result["react"])
        self.assertEqual(result["final"], "hi")

    def test_native_no_fallback_when_mode_native(self):
        def scripted(body, turn):
            return web.Response(status=400, text="tools not supported")

        async def run(session, base):
            messages = [{"role": "user", "content": "hi"}]
            return await agent.run_loop(session, base, "", "m", messages,
                                        tool_impls={"echo": lambda a, s: "x"},
                                        tool_mode="native")

        with self.assertRaises(RuntimeError):
            asyncio.run(_with_server(self._tool_app(scripted), run))

    def test_max_iterations_cap(self):
        def scripted(body, turn):
            return delta_response(tool_call_chunks("loop", "{}", call_id="c{}".format(turn)))

        async def loop_impl(args, session):
            return "again"

        async def run(session, base):
            messages = [{"role": "user", "content": "loop"}]
            return await agent.run_loop(session, base, "", "m", messages,
                                        tool_impls={"loop": loop_impl},
                                        max_iterations=3)

        result = asyncio.run(_with_server(self._tool_app(scripted), run))
        self.assertEqual(result["iterations"], 3)
        self.assertEqual(result["final"], "working")

    def test_redirect_surfaces_as_provider_error(self):
        def scripted(body, turn):
            return web.Response(status=302, headers={"Location": "/elsewhere"})

        async def run(session, base):
            messages = [{"role": "user", "content": "hi"}]
            return await agent.run_loop(session, base, "sk", "m", messages,
                                        tool_impls={"echo": lambda a, s: "x"},
                                        max_iterations=2)

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(_with_server(self._tool_app(scripted), run))
        self.assertIn("redirect", str(ctx.exception))
        self.assertNotIn("tool", str(ctx.exception).lower())

    def test_exhaustion_closing_turn_without_tools(self):
        requests = []

        def scripted(body, turn):
            requests.append({"body": body, "messages": list(body["messages"])})
            if "tools" not in body:
                return delta_response([{"content": "closing answer"}])
            return delta_response(tool_call_chunks("loop", "{}", call_id="c{}".format(turn)))

        async def loop_impl(args, session):
            return "again"

        async def run(session, base):
            messages = [{"role": "user", "content": "loop"}]
            return await agent.run_loop(session, base, "", "m", messages,
                                        tool_impls={"loop": loop_impl},
                                        max_iterations=2)

        result = asyncio.run(_with_server(self._tool_app(scripted), run))
        self.assertEqual(len(requests), 3)
        closing = requests[-1]
        self.assertNotIn("tools", closing["body"])
        self.assertNotIn("tool_choice", closing["body"])
        instructions = [m for m in closing["messages"]
                        if m.get("role") == "user" and "Iteration budget exhausted" in (m.get("content") or "")]
        self.assertEqual(len(instructions), 1)
        self.assertEqual(result["final"], "closing answer")
        self.assertEqual(result["iterations"], 2)

    def test_exhaustion_empty_closing_turn(self):
        def scripted(body, turn):
            if "tools" not in body:
                return delta_response([{"content": ""}])
            return delta_response(tool_call_chunks("loop", "{}", call_id="c{}".format(turn)))

        async def loop_impl(args, session):
            return "again"

        async def run(session, base):
            messages = [{"role": "user", "content": "loop"}]
            return await agent.run_loop(session, base, "", "m", messages,
                                        tool_impls={"loop": loop_impl},
                                        max_iterations=2)

        result = asyncio.run(_with_server(self._tool_app(scripted), run))
        self.assertTrue(result["final"].startswith("[max iterations reached]"))
        self.assertIn("working", result["final"])

    def test_web_search_then_web_fetch_sequence(self):
        def scripted(body, turn):
            if turn == 0:
                declared = [t["function"]["name"] for t in body["tools"]]
                assert "web_search" in declared and "web_fetch" in declared
                return delta_response(
                    tool_call_chunks("web_search", '{"query": "qwen3 lora"}', call_id="c1"))
            if turn == 1:
                return delta_response(
                    tool_call_chunks("web_fetch", '{"url": "http://x/card"}', call_id="c2"))
            return delta_response([{"content": "done"}])

        order = []

        async def search_impl(args, session):
            order.append(("web_search", args))
            return "1. Card\n   http://x/card"

        async def fetch_impl(args, session):
            order.append(("web_fetch", args))
            return "card text"

        async def run(session, base):
            messages = [{"role": "user", "content": "find triggers"}]
            return await agent.run_loop(session, base, "", "m", messages,
                                        tool_impls={"web_search": search_impl, "web_fetch": fetch_impl},
                                        max_iterations=4)

        result = asyncio.run(_with_server(self._tool_app(scripted), run))
        self.assertEqual(result["final"], "done")
        self.assertEqual(result["iterations"], 3)
        self.assertEqual(order, [("web_search", {"query": "qwen3 lora"}),
                                 ("web_fetch", {"url": "http://x/card"})])

    def test_tool_error_reported_to_model(self):
        def scripted(body, turn):
            if turn == 0:
                return delta_response(tool_call_chunks("boom", "{}"))
            return delta_response([{"content": "recovered"}])

        async def boom_impl(args, session):
            raise RuntimeError("kaboom")

        async def run(session, base):
            messages = [{"role": "user", "content": "x"}]
            return await agent.run_loop(session, base, "", "m", messages,
                                        tool_impls={"boom": boom_impl})

        result = asyncio.run(_with_server(self._tool_app(scripted), run))
        self.assertEqual(result["final"], "recovered")
        tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
        self.assertIn("TOOL ERROR: RuntimeError: kaboom", tool_msgs[0]["content"])


class WebFetchTests(unittest.TestCase):
    def test_local_access_requires_opt_in_and_preserves_safety_checks(self):
        hits = []
        async def handler(request):
            hits.append(request.path)
            return web.Response(text="local response")
        async def redirect(request):
            raise web.HTTPFound("/page")
        async def run(session, base):
            with self.assertRaises(ValueError):
                await webfetch.fetch_text(session, base + "/page")
            self.assertEqual(await webfetch.fetch_text(session, base + "/page", allow_local=True),
                             "local response")
            localhost = base.replace("127.0.0.1", "localhost")
            self.assertEqual(await webfetch.fetch_text(session, localhost + "/page",
                                                       max_chars=5, allow_local=True),
                             "local\n...[truncated]")
            for url in ("file:///etc/passwd", base.replace("http://", "http://user:pass@")):
                with self.assertRaises(ValueError):
                    await webfetch.fetch_text(session, url, allow_local=True)
            with self.assertRaisesRegex(RuntimeError, "redirect"):
                await webfetch.fetch_text(session, base + "/redirect", allow_local=True)
        app = web.Application()
        app.router.add_get("/page", handler)
        app.router.add_get("/redirect", redirect)
        asyncio.run(_with_server(app, run))
        self.assertEqual(hits, ["/page", "/page"])

    def test_strips_html(self):
        async def handler(request):
            return web.Response(
                text="<html><head><style>p{color:red}</style></head><body><script>x()</script>"
                     "<p>Hi <b>there</b></p><p>Second para</p></body></html>",
                content_type="text/html")

        async def run(session, base):
            return await webfetch.fetch_text(session, base.rstrip("/") + "/page")

        app = web.Application()
        app.router.add_get("/page", handler)
        with patch.object(webfetch, "_public_ip", return_value=True):
            text = asyncio.run(_with_server(app, run))
        self.assertEqual(text, "Hi there\nSecond para")

    def test_rejects_non_http(self):
        async def run(session, base):
            return await webfetch.fetch_text(session, "file:///etc/passwd")

        app = web.Application()
        with self.assertRaises(ValueError):
            asyncio.run(_with_server(app, run))

    def test_non_200(self):
        async def handler(request):
            return web.Response(status=404)

        async def run(session, base):
            return await webfetch.fetch_text(session, base.rstrip("/") + "/missing")

        app = web.Application()
        app.router.add_get("/missing", handler)
        with patch.object(webfetch, "_public_ip", return_value=True):
            with self.assertRaises(RuntimeError):
                asyncio.run(_with_server(app, run))

    def test_truncation(self):
        async def handler(request):
            return web.Response(text="a" * 500, content_type="text/plain")

        async def run(session, base):
            return await webfetch.fetch_text(session, base.rstrip("/") + "/big", max_chars=100)

        app = web.Application()
        app.router.add_get("/big", handler)
        with patch.object(webfetch, "_public_ip", return_value=True):
            text = asyncio.run(_with_server(app, run))
        self.assertTrue(text.endswith("[truncated]"))
        self.assertEqual(len(text), len("a" * 100) + len("\n...[truncated]"))

    def test_private_addresses_are_rejected(self):
        async def run(session, base):
            for url in (base, "http://localhost/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
                        "http://169.254.169.254/", "http://user:pass@example.com/"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    await webfetch.fetch_text(session, url)
        asyncio.run(_with_server(web.Application(), run))

    def test_redirects_are_not_followed(self):
        hits = []
        async def redirect(request):
            return web.Response(status=302, headers={"Location": "/private"})
        async def private(request):
            hits.append(True)
            return web.Response(text="private")
        async def run(session, base):
            with patch.object(webfetch, "_public_ip", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "redirect"):
                    await webfetch.fetch_text(session, base + "/redirect")
        app = web.Application()
        app.router.add_get("/redirect", redirect)
        app.router.add_get("/private", private)
        asyncio.run(_with_server(app, run))
        self.assertEqual(hits, [])

    def test_dns_rejects_private_and_mixed_addresses(self):
        async def run():
            resolver = webfetch.PublicResolver()
            try:
                with patch.object(resolver._resolver, "resolve", return_value=[
                    {"host": "8.8.8.8"}, {"host": "127.0.0.1"}
                ]):
                    with self.assertRaisesRegex(ValueError, "public"):
                        await resolver.resolve("example.com", 80)
            finally:
                await resolver.close()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
