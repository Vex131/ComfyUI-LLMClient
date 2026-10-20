import asyncio
import json
import unittest

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
        with self.assertRaises(RuntimeError):
            asyncio.run(_with_server(app, run))

    def test_truncation(self):
        async def handler(request):
            return web.Response(text="a" * 500, content_type="text/plain")

        async def run(session, base):
            return await webfetch.fetch_text(session, base.rstrip("/") + "/big", max_chars=100)

        app = web.Application()
        app.router.add_get("/big", handler)
        text = asyncio.run(_with_server(app, run))
        self.assertTrue(text.endswith("[truncated]"))
        self.assertEqual(len(text), len("a" * 100) + len("\n...[truncated]"))


if __name__ == "__main__":
    unittest.main()
