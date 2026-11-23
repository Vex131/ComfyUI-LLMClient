import os

import aiohttp
from aiohttp import web
from server import PromptServer
from urllib.parse import urlsplit

from . import config
from .providers import client, credstore, websearch

_MAX_API_KEY_LEN = 512
_SEARCH_ID = "search.tavily_api_key"
_SEARXNG_ID = "search.searxng_url"
_LLM_PREFIX = "llm."
_LLM_NAME_PREFIX = "llm-name."


@PromptServer.instance.routes.post("/llmclient/test")
async def test_connection(request: web.Request) -> web.Response:
    body = await request.json()
    base_url = body.get("base_url", "")

    try:
        api_key = client.resolve_key(base_url)
    except ValueError as exc:
        return web.json_response({"ok": False, "profile": "generic", "models": [], "context": None,
                                  "error": str(exc)})

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        result = await client.probe(session, base_url, api_key)

    if result["ok"]:
        config.update_cache(base_url, result["profile"], result["models"], context=result.get("context"))
    return web.json_response(result)


def _entry_state(secret_id: str) -> dict:
    return {"set": credstore.get_secret(secret_id) is not None, "preview": credstore.preview(secret_id)}


def _status_payload() -> dict:
    return {
        "backend": credstore.BACKEND,
        "fallback_reason": credstore.FALLBACK_REASON,
        "notices": list(credstore.STORE_NOTICES),
        "search": dict(_entry_state(_SEARCH_ID),
                       searxng_url=credstore.get_secret(_SEARXNG_ID) or "",
                       searxng_env=bool(os.environ.get("SEARXNG_URL", "").strip())),
        "llm": [{"base_url": secret_id[len(_LLM_PREFIX):], "preview": credstore.preview(secret_id),
                 "name": credstore.get_secret(_LLM_NAME_PREFIX + secret_id[len(_LLM_PREFIX):]) or ""}
                for secret_id in credstore.list_ids(_LLM_PREFIX)],
    }


@PromptServer.instance.routes.get("/llmclient/secrets/status")
async def secrets_status(request: web.Request) -> web.Response:
    return web.json_response(_status_payload())


def _api_key_field(body: dict):
    if "api_key" not in body:
        return None, None
    api_key = body["api_key"]
    if not isinstance(api_key, str):
        return None, {"error": "api_key must be a string"}
    if len(api_key) > _MAX_API_KEY_LEN:
        return None, {"error": "api_key too long (max {} characters)".format(_MAX_API_KEY_LEN)}
    return api_key, None


@PromptServer.instance.routes.post("/llmclient/secrets/llm")
async def secrets_set_llm(request: web.Request) -> web.Response:
    body = await request.json()
    base_url = body.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        return web.json_response({"error": "base_url is required"}, status=400)
    root = client.normalize_root(base_url)
    if not urlsplit(root).hostname:
        return web.json_response({"error": "invalid base_url"}, status=400)

    api_key, error = _api_key_field(body)
    if error:
        return web.json_response(error, status=400)
    name = body.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > 100):
        return web.json_response({"error": "name must be a string of at most 100 characters"}, status=400)

    secret_id = _LLM_PREFIX + root
    name_id = _LLM_NAME_PREFIX + root
    if api_key is not None:
        if api_key and not client.transport_is_safe(root):
            return web.json_response({"error": "refusing to store credentials for a plaintext HTTP "
                                               "public host; use https or a private-network address"}, status=400)
        if api_key:
            credstore.set_secret(secret_id, api_key)
            if name is not None:
                if name.strip():
                    credstore.set_secret(name_id, name.strip())
                else:
                    credstore.delete_secret(name_id)
        else:
            credstore.delete_secret(secret_id)
            credstore.delete_secret(name_id)

    state = _entry_state(secret_id)
    return web.json_response({"base_url": root, "set": state["set"], "preview": state["preview"]})


@PromptServer.instance.routes.post("/llmclient/secrets/search")
async def secrets_set_search(request: web.Request) -> web.Response:
    body = await request.json()
    api_key, error = _api_key_field(body)
    if error:
        return web.json_response(error, status=400)
    searxng_url = body.get("searxng_url")
    if searxng_url is not None:
        if not isinstance(searxng_url, str) or len(searxng_url) > 2048:
            return web.json_response({"error": "SearXNG URL must be a string of at most 2048 characters"}, status=400)
        searxng_url = searxng_url.strip().rstrip("/")
        if searxng_url:
            try:
                parts = urlsplit(searxng_url)
                valid = (parts.scheme in ("http", "https") and parts.hostname and
                         parts.username is None and parts.password is None and
                         not parts.query and not parts.fragment and parts.port != 0 and
                         not any(ch.isspace() for ch in searxng_url))
            except ValueError:
                valid = False
            if not valid:
                return web.json_response({"error": "Enter a valid HTTP or HTTPS SearXNG URL without credentials, query or fragment"}, status=400)

    if api_key is not None:
        if api_key:
            credstore.set_secret(_SEARCH_ID, api_key)
        else:
            credstore.delete_secret(_SEARCH_ID)
    if searxng_url is not None:
        if searxng_url:
            credstore.set_secret(_SEARXNG_ID, searxng_url)
        else:
            credstore.delete_secret(_SEARXNG_ID)
    if api_key is not None or searxng_url is not None:
        websearch._reset_resilience_state()

    state = _entry_state(_SEARCH_ID)
    return web.json_response({"search": {"set": state["set"], "preview": state["preview"]}})
