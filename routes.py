import aiohttp
from aiohttp import web
from server import PromptServer

from . import config
from .providers import client


@PromptServer.instance.routes.post("/llmclient/test")
async def test_connection(request: web.Request) -> web.Response:
    body = await request.json()
    base_url = body.get("base_url", "")
    api_key = body.get("api_key", "")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        result = await client.probe(session, base_url, api_key)

    if result["ok"]:
        config.update_cache(base_url, result["profile"], result["models"], context=result.get("context"))
    return web.json_response(result)
