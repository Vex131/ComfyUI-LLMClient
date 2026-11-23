import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from providers import client, websearch


def load_routes(store):
    server = ModuleType("server")
    routes = SimpleNamespace(get=lambda path: lambda fn: fn, post=lambda path: lambda fn: fn)
    server.PromptServer = SimpleNamespace(instance=SimpleNamespace(routes=routes))
    package = ModuleType("name_test")
    package.__path__ = []
    providers = ModuleType("name_test.providers")
    providers.client = client
    providers.credstore = store
    providers.websearch = websearch
    config = ModuleType("name_test.config")
    modules = {"server": server, "name_test": package, "name_test.providers": providers,
               "name_test.config": config}
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("name_test.routes", Path(__file__).resolve().parents[1] / "routes.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class ProviderNameTests(unittest.TestCase):
    def test_searxng_configuration_validates_and_resets_search_cache(self):
        data = {}
        store = SimpleNamespace(
            BACKEND="test", FALLBACK_REASON=None, STORE_NOTICES=[],
            get_secret=lambda key: data.get(key),
            set_secret=lambda key, value: data.__setitem__(key, value),
            delete_secret=lambda key: data.pop(key, None),
            list_ids=lambda prefix: [],
            preview=lambda key: "",
        )
        routes = load_routes(store)

        async def post(body):
            async def request_json():
                return body
            return await routes.secrets_set_search(SimpleNamespace(json=request_json))

        with patch.object(websearch, "_reset_resilience_state") as reset:
            response = asyncio.run(post({"searxng_url": " http://localhost:8888/ "}))
            self.assertEqual(response.status, 200)
            self.assertEqual(routes._status_payload()["search"]["searxng_url"], "http://localhost:8888")
            self.assertEqual(reset.call_count, 1)
            for invalid in ("file:///etc/passwd", "http://user:pass@example.com", "https://example.com/?key=x", "http://localhost:bad"):
                self.assertEqual(asyncio.run(post({"searxng_url": invalid})).status, 400)
            self.assertEqual(reset.call_count, 1)
            self.assertEqual(asyncio.run(post({"searxng_url": ""})).status, 200)
            self.assertNotIn("search.searxng_url", data)
            self.assertEqual(reset.call_count, 2)

    def test_names_are_optional_persistent_and_removed_with_key(self):
        data = {}
        store = SimpleNamespace(
            BACKEND="test", FALLBACK_REASON=None, STORE_NOTICES=[],
            get_secret=lambda key: data.get(key),
            set_secret=lambda key, value: data.__setitem__(key, value),
            delete_secret=lambda key: data.pop(key, None),
            list_ids=lambda prefix: sorted(key for key in data if key.startswith(prefix)),
            preview=lambda key: "masked" if key in data else "",
        )
        routes = load_routes(store)

        async def post(body):
            async def request_json():
                return body
            response = await routes.secrets_set_llm(SimpleNamespace(json=request_json))
            return response.status, json.loads(response.text)

        url = "http://localhost:1234/v1"
        self.assertEqual(asyncio.run(post({"base_url": url, "api_key": "secret", "name": " Local "}))[0], 200)
        self.assertEqual(routes._status_payload()["llm"][0]["name"], "Local")
        self.assertEqual(data["llm." + url], "secret")
        asyncio.run(post({"base_url": url, "api_key": "new"}))
        self.assertEqual(routes._status_payload()["llm"][0]["name"], "Local")
        self.assertEqual(asyncio.run(post({"base_url": url, "api_key": "bad", "name": "x" * 101}))[0], 400)
        self.assertEqual(data["llm." + url], "new")
        asyncio.run(post({"base_url": url, "api_key": "new", "name": ""}))
        self.assertEqual(routes._status_payload()["llm"][0]["name"], "")
        asyncio.run(post({"base_url": url, "api_key": "new", "name": "Again"}))
        asyncio.run(post({"base_url": url, "api_key": ""}))
        self.assertEqual(data, {})


if __name__ == "__main__":
    unittest.main()
