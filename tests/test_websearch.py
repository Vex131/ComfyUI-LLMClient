import asyncio
import unittest

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

        with self.assertRaises(RuntimeError):
            asyncio.run(go())

    def test_schema_registered(self):
        schema = agent.tool_schemas(["web_search"])[0]
        self.assertEqual(schema["function"]["name"], "web_search")
        self.assertEqual(schema["function"]["parameters"]["required"], ["query"])


if __name__ == "__main__":
    unittest.main()
