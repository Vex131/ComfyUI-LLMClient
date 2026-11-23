import asyncio
import os
import unittest
from unittest import mock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from providers import agent, websearch

HTML_RESULTS = """
<html><body>
<div class="result results_links">
  <h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fcard&amp;rut=deadbeef">Qwen3 <b>Model Card</b></a></h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fcard">Triggers: <b>words</b> here</a>
</div>
<div class="result results_links">
  <h2 class="result__title"><a rel="nofollow" class="result__a" href="https://direct.example.org/docs">Direct Docs</a></h2>
  <td class="result__snippet">Snippet two</td>
</div>
</body></html>
"""

LITE_RESULTS = """
<html><body><table>
<tr><td><a rel="nofollow" href="https://lite.example.org/a" class="result-link">Lite <i>Result</i></a></td></tr>
<tr><td class="result-snippet">Lite snippet</td></tr>
</table></body></html>
"""

JUNK = "<html><body><p>anomaly challenge, no results here</p></body></html>"


def _isolate(case):
    websearch._reset_resilience_state()
    case.addCleanup(websearch._reset_resilience_state)
    env = mock.patch.dict(os.environ, {"TAVILY_API_KEY": "", "SEARXNG_URL": ""})
    env.start()
    case.addCleanup(env.stop)
    cred = mock.patch.object(websearch, "_credstore_secret", lambda name: "")
    cred.start()
    case.addCleanup(cred.stop)


class _FakeClock:
    def __init__(self, start=0.0):
        self.t = float(start)

    def __call__(self):
        return self.t


def _run(pages, query, lite_url=""):
    calls = []

    async def handler(request):
        calls.append(dict(request.query))
        page = pages[len(calls) - 1] if len(calls) <= len(pages) else ""
        return web.Response(text=page, content_type="text/html")

    app = web.Application()
    app.router.add_get("/search", handler)

    async def go():
        server = TestServer(app)
        await server.start_server()
        try:
            async with aiohttp.ClientSession() as session:
                base = str(server.make_url("/search"))
                lite = base if lite_url == "same" else lite_url
                out = await websearch.search(session, query, base_url=base, lite_url=lite)
                return out, calls
        finally:
            await server.close()

    return asyncio.run(go())


class WebSearchTests(unittest.TestCase):
    def setUp(self):
        _isolate(self)

    def test_parses_and_decodes_uddg(self):
        out, _ = _run([HTML_RESULTS], "qwen3 lora")
        self.assertIn("1. Qwen3 Model Card", out)
        self.assertIn("https://example.com/card", out)
        self.assertIn("Triggers: words here", out)
        self.assertIn("2. Direct Docs", out)
        self.assertIn("https://direct.example.org/docs", out)
        self.assertIn("Snippet two", out)

    def test_query_param_sent(self):
        _, calls = _run([HTML_RESULTS], "  qwen3 trigger  ")
        self.assertEqual(calls[0]["q"], "qwen3 trigger")

    def test_falls_back_to_lite(self):
        out, calls = _run([JUNK, LITE_RESULTS], "x", lite_url="same")
        self.assertEqual(len(calls), 2)
        self.assertIn("1. Lite Result", out)
        self.assertIn("https://lite.example.org/a", out)
        self.assertIn("Lite snippet", out)

    def test_no_results_message(self):
        out, _ = _run([JUNK], "anything")
        self.assertIn("NO RESULTS", out)

    def test_empty_query_rejected(self):
        with self.assertRaises(ValueError):
            _run([HTML_RESULTS], "   ")

    def test_http_error(self):
        async def handler(request):
            return web.Response(status=403)

        app = web.Application()
        app.router.add_get("/search", handler)

        async def go():
            server = TestServer(app)
            await server.start_server()
            try:
                async with aiohttp.ClientSession() as session:
                    base = str(server.make_url("/search"))
                    return await websearch.search(session, "q", base_url=base, lite_url="")
            finally:
                await server.close()

        out = asyncio.run(go())
        self.assertIn("SEARCH UNAVAILABLE", out)
        self.assertIn("403", out)

    def test_schema_registered(self):
        schema = agent.tool_schemas(["web_search"])[0]
        self.assertEqual(schema["function"]["name"], "web_search")
        self.assertEqual(schema["function"]["parameters"]["required"], ["query"])
        self.assertEqual(schema["function"]["parameters"]["properties"]["query"]["type"], "string")


class WebSearchResilienceTests(unittest.TestCase):
    def setUp(self):
        _isolate(self)

    def test_saved_searxng_url_takes_priority_over_environment(self):
        async def go():
            with mock.patch.object(websearch, "_credstore_secret",
                                   side_effect=lambda name: "http://saved:8888" if name == "search.searxng_url" else ""), \
                 mock.patch.dict(os.environ, {"SEARXNG_URL": "http://environment:8888"}), \
                 mock.patch.object(websearch, "_searxng_attempt", new_callable=mock.AsyncMock,
                                   return_value=[{"title": "Saved", "url": "https://example.com", "snippet": ""}]) as attempt:
                result = await websearch.search(None, "saved url test")
                self.assertIn("Saved", result)
                self.assertEqual(attempt.call_args.args[1], "http://saved:8888")
        asyncio.run(go())

    def test_tavily_success_sends_bearer_and_maps_results(self):
        seen = {}

        async def handler(request):
            seen["auth"] = request.headers.get("Authorization", "")
            seen["body"] = await request.json()
            return web.json_response({"results": [
                {"title": "Tavily One", "url": "https://t.example/1", "content": "Tavily snippet"},
                {"title": "Tavily Two", "url": "https://t.example/2", "content": "Tavily snippet two"},
            ]})

        app = web.Application()
        app.router.add_post("/search", handler)

        async def go():
            server = TestServer(app)
            await server.start_server()
            try:
                async with aiohttp.ClientSession() as session:
                    with mock.patch.object(websearch, "TAVILY_URL", str(server.make_url("/search"))):
                        return await websearch.search(session, "tavily query", tavily_api_key="tv-key")
            finally:
                await server.close()

        out = asyncio.run(go())
        self.assertEqual(seen["auth"], "Bearer tv-key")
        self.assertEqual(seen["body"]["query"], "tavily query")
        self.assertEqual(seen["body"]["max_results"], 6)
        self.assertEqual(seen["body"]["search_depth"], "basic")
        self.assertIn("1. Tavily One", out)
        self.assertIn("https://t.example/1", out)
        self.assertIn("Tavily snippet", out)
        self.assertIn("2. Tavily Two", out)

    def test_tavily_first_then_skipped_while_in_cooldown(self):
        hits = []

        async def tavily_handler(request):
            hits.append("tavily")
            return web.Response(status=500)

        async def ddg_handler(request):
            hits.append("duckduckgo")
            return web.Response(text=HTML_RESULTS, content_type="text/html")

        app = web.Application()
        app.router.add_post("/tavily", tavily_handler)
        app.router.add_get("/html", ddg_handler)

        clock = _FakeClock(1000.0)

        async def go():
            server = TestServer(app)
            await server.start_server()
            try:
                async with aiohttp.ClientSession() as session:
                    tavily = str(server.make_url("/tavily"))
                    ddg = str(server.make_url("/html"))
                    with mock.patch.object(websearch, "TAVILY_URL", tavily):
                        out1 = await websearch.search(session, "alpha", base_url=ddg, lite_url="",
                                                      tavily_api_key="k", clock=clock)
                        clock.t += 30
                        out2 = await websearch.search(session, "beta", base_url=ddg, lite_url="",
                                                      tavily_api_key="k", clock=clock)
                    return out1, out2
            finally:
                await server.close()

        out1, out2 = asyncio.run(go())
        self.assertEqual(hits, ["tavily", "duckduckgo", "duckduckgo"])
        self.assertIn("1. Qwen3 Model Card", out1)
        self.assertIn("1. Qwen3 Model Card", out2)

    def test_backoff_growth_across_repeated_failures(self):
        hits = []

        async def tavily_handler(request):
            hits.append("tavily")
            return web.Response(status=500)

        async def ddg_handler(request):
            return web.Response(text=HTML_RESULTS, content_type="text/html")

        app = web.Application()
        app.router.add_post("/tavily", tavily_handler)
        app.router.add_get("/html", ddg_handler)

        clock = _FakeClock(0.0)

        async def go():
            server = TestServer(app)
            await server.start_server()
            try:
                async with aiohttp.ClientSession() as session:
                    tavily = str(server.make_url("/tavily"))
                    ddg = str(server.make_url("/html"))
                    with mock.patch.object(websearch, "TAVILY_URL", tavily):
                        for i, t in enumerate((0.0, 59.0, 61.0, 180.0, 182.0)):
                            clock.t = t
                            await websearch.search(session, "backoff {}".format(i), base_url=ddg,
                                                   lite_url="", tavily_api_key="k", clock=clock)
            finally:
                await server.close()

        asyncio.run(go())
        self.assertEqual(len(hits), 3)

    def test_searxng_json_mapping_without_auth_header(self):
        seen = []

        async def sx_handler(request):
            seen.append(request)
            return web.json_response({"results": [
                {"title": "Sx One", "url": "http://sx.example/1", "content": "Sx body"},
                {"title": "Sx Two", "url": "http://sx.example/2", "content": "Sx body two"},
            ]})

        async def ddg_handler(request):
            return web.Response(text=JUNK, content_type="text/html")

        app = web.Application()
        app.router.add_get("/sx/search", sx_handler)
        app.router.add_get("/html", ddg_handler)

        async def go():
            server = TestServer(app)
            await server.start_server()
            try:
                async with aiohttp.ClientSession() as session:
                    out = await websearch.search(session, "sx query",
                                                 base_url=str(server.make_url("/html")), lite_url="",
                                                 searxng_url=str(server.make_url("/sx")))
                    return out
            finally:
                await server.close()

        out = asyncio.run(go())
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].query.get("format"), "json")
        self.assertNotIn("Authorization", seen[0].headers)
        self.assertIn("1. Sx One", out)
        self.assertIn("http://sx.example/1", out)
        self.assertIn("Sx body", out)
        self.assertIn("2. Sx Two", out)

    def test_searxng_403_hint_and_cooldown(self):
        sx_hits = []
        ddg_hits = []

        async def sx_handler(request):
            sx_hits.append(1)
            return web.Response(status=403)

        async def ddg_handler(request):
            ddg_hits.append(1)
            return web.Response(status=500)

        app = web.Application()
        app.router.add_get("/sx/search", sx_handler)
        app.router.add_get("/html", ddg_handler)

        clock = _FakeClock(0.0)

        async def go():
            server = TestServer(app)
            await server.start_server()
            try:
                async with aiohttp.ClientSession() as session:
                    kw = {"base_url": str(server.make_url("/html")), "lite_url": "",
                          "searxng_url": str(server.make_url("/sx")), "clock": clock}
                    out1 = await websearch.search(session, "sx a", **kw)
                    clock.t = 100.0
                    out2 = await websearch.search(session, "sx b", **kw)
                    return out1, out2
            finally:
                await server.close()

        out1, out2 = asyncio.run(go())
        self.assertIn("SEARCH UNAVAILABLE", out1)
        self.assertIn("settings.yml", out1)
        self.assertIn("SEARCH UNAVAILABLE", out2)
        self.assertEqual(sx_hits, [1])
        self.assertEqual(ddg_hits, [1, 1])

    def test_cache_same_query_single_http_call(self):
        hits = []

        async def ddg_handler(request):
            hits.append(1)
            return web.Response(text=HTML_RESULTS, content_type="text/html")

        app = web.Application()
        app.router.add_get("/html", ddg_handler)

        async def go():
            server = TestServer(app)
            await server.start_server()
            try:
                async with aiohttp.ClientSession() as session:
                    ddg = str(server.make_url("/html"))
                    out1 = await websearch.search(session, "Cached Query", base_url=ddg, lite_url="")
                    out2 = await websearch.search(session, "cached query", base_url=ddg, lite_url="")
                    return out1, out2
            finally:
                await server.close()

        out1, out2 = asyncio.run(go())
        self.assertEqual(out1, out2)
        self.assertEqual(len(hits), 1)
        self.assertIn("1. Qwen3 Model Card", out1)

    def test_tavily_redirect_not_followed_falls_through_with_cooldown(self):
        tavily_hits = []
        followed_hits = []
        ddg_hits = []

        async def tavily_handler(request):
            tavily_hits.append(1)
            return web.Response(status=302, headers={"Location": "/followed"})

        async def followed_handler(request):
            followed_hits.append(1)
            return web.json_response({"results": [
                {"title": "Leaked", "url": "http://leaked.example", "content": "no"}]})

        async def ddg_handler(request):
            ddg_hits.append(1)
            return web.Response(text=HTML_RESULTS, content_type="text/html")

        app = web.Application()
        app.router.add_post("/tavily", tavily_handler)
        app.router.add_post("/followed", followed_handler)
        app.router.add_get("/html", ddg_handler)

        clock = _FakeClock(0.0)

        async def go():
            server = TestServer(app)
            await server.start_server()
            try:
                async with aiohttp.ClientSession() as session:
                    ddg = str(server.make_url("/html"))
                    with mock.patch.object(websearch, "TAVILY_URL", str(server.make_url("/tavily"))):
                        out1 = await websearch.search(session, "redir one", base_url=ddg, lite_url="",
                                                      tavily_api_key="k", clock=clock)
                        clock.t = 30.0
                        out2 = await websearch.search(session, "redir two", base_url=ddg, lite_url="",
                                                      tavily_api_key="k", clock=clock)
                        return out1, out2
            finally:
                await server.close()

        out1, out2 = asyncio.run(go())
        self.assertIn("1. Qwen3 Model Card", out1)
        self.assertIn("1. Qwen3 Model Card", out2)
        self.assertEqual(tavily_hits, [1])
        self.assertEqual(followed_hits, [])
        self.assertEqual(ddg_hits, [1, 1])

    def test_timeout_feeds_cooldown_and_unavailable_message(self):
        ddg_hits = []

        async def ddg_handler(request):
            ddg_hits.append(1)
            await asyncio.sleep(3)
            return web.Response(text=HTML_RESULTS, content_type="text/html")

        app = web.Application()
        app.router.add_get("/html", ddg_handler)

        clock = _FakeClock(0.0)

        async def go():
            server = TestServer(app)
            await server.start_server()
            try:
                async with aiohttp.ClientSession() as session:
                    kw = {"base_url": str(server.make_url("/html")), "lite_url": "",
                          "timeout": 1, "clock": clock}
                    out1 = await websearch.search(session, "slow one", **kw)
                    clock.t = 30.0
                    out2 = await websearch.search(session, "slow two", **kw)
                    return out1, out2
            finally:
                await server.close()

        out1, out2 = asyncio.run(go())
        self.assertIn("SEARCH UNAVAILABLE", out1)
        self.assertIn("timeout after 1s", out1)
        self.assertIn("Configure TAVILY_API_KEY", out1)
        self.assertIn("SEARCH UNAVAILABLE", out2)
        self.assertIn("cooling down", out2)
        self.assertEqual(ddg_hits, [1])

    def test_all_engines_unavailable_returns_string_no_raise(self):
        async def ddg_handler(request):
            return web.Response(status=202)

        app = web.Application()
        app.router.add_get("/html", ddg_handler)

        async def go():
            server = TestServer(app)
            await server.start_server()
            try:
                async with aiohttp.ClientSession() as session:
                    return await websearch.search(session, "blocked query",
                                                  base_url=str(server.make_url("/html")), lite_url="")
            finally:
                await server.close()

        out = asyncio.run(go())
        self.assertIn("SEARCH UNAVAILABLE", out)
        self.assertIn("Configure TAVILY_API_KEY", out)
        self.assertIn("SearXNG", out)
        self.assertIn("web_fetch", out)
        self.assertIn("retry later", out)

    def test_ddg_202_blocks_with_lite_chain_intact(self):
        html_hits = []
        lite_hits = []

        async def html_handler(request):
            html_hits.append(1)
            return web.Response(status=202)

        async def lite_handler(request):
            lite_hits.append(1)
            return web.Response(text=LITE_RESULTS, content_type="text/html")

        app = web.Application()
        app.router.add_get("/html", html_handler)
        app.router.add_get("/lite", lite_handler)

        clock = _FakeClock(0.0)

        async def go():
            server = TestServer(app)
            await server.start_server()
            try:
                async with aiohttp.ClientSession() as session:
                    out1 = await websearch.search(session, "chain one",
                                                  base_url=str(server.make_url("/html")),
                                                  lite_url=str(server.make_url("/lite")), clock=clock)
                    clock.t = 60.0
                    out2 = await websearch.search(session, "chain two",
                                                  base_url=str(server.make_url("/html")),
                                                  lite_url=str(server.make_url("/lite")), clock=clock)
                    return out1, out2
            finally:
                await server.close()

        out1, out2 = asyncio.run(go())
        self.assertIn("1. Lite Result", out1)
        self.assertEqual(html_hits, [1])
        self.assertEqual(lite_hits, [1])
        self.assertIn("SEARCH UNAVAILABLE", out2)
        self.assertEqual(html_hits, [1])
        self.assertEqual(lite_hits, [1])


if __name__ == "__main__":
    unittest.main()
