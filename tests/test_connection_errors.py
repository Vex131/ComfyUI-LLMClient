import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import aiohttp

from providers.client import provider_http_error, stream_post
from providers import agent, client


class FailingRequest:
    def __init__(self, error):
        self.error = error

    async def __aenter__(self):
        raise self.error

    async def __aexit__(self, *args):
        pass


class Response:
    status = 401

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def text(self):
        return "invalid key"


class ConnectionErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_failure_has_safe_actionable_message(self):
        error = aiohttp.ClientConnectorError(MagicMock(), OSError("refused"))
        session = SimpleNamespace(post=lambda *args, **kwargs: FailingRequest(error))
        with self.assertRaises(RuntimeError) as caught:
            async with stream_post(session, "http://user:secret@127.0.0.1:18100/v1/chat/completions",
                                   "", {}):
                pass
        self.assertIn("provider cannot be reached at 127.0.0.1:18100", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    async def test_certificate_error_is_not_misreported(self):
        error = aiohttp.ClientConnectorSSLError(MagicMock(), OSError("certificate"))
        session = SimpleNamespace(post=lambda *args, **kwargs: FailingRequest(error))
        with self.assertRaises(aiohttp.ClientConnectorSSLError):
            async with stream_post(session, "https://example.com/v1/chat/completions", "", {}):
                pass

    async def test_invalid_url_points_to_provider_settings(self):
        session = SimpleNamespace(post=lambda *args, **kwargs: FailingRequest(aiohttp.InvalidURL("bad")))
        with self.assertRaisesRegex(ValueError, "Invalid LLM Provider URL"):
            async with stream_post(session, "bad", "", {}):
                pass

    async def test_timeout_is_actionable_and_remains_timeout_error(self):
        session = SimpleNamespace(post=lambda *args, **kwargs: FailingRequest(TimeoutError()))
        with self.assertRaisesRegex(TimeoutError, "timeout_seconds"):
            async with stream_post(session, "http://localhost:18100/v1/chat/completions", "", {}):
                pass

    def test_http_errors_include_guidance_and_provider_details(self):
        for status, hint in ((401, "API key"), (403, "permissions"), (404, "model"),
                             (429, "quota"), (503, "server logs")):
            with self.subTest(status=status):
                message = str(provider_http_error(status, "provider detail"))
                self.assertIn("HTTP {}".format(status), message)
                self.assertIn(hint, message)
                self.assertIn("provider detail", message)

    async def test_chat_and_agent_show_authentication_guidance(self):
        session = SimpleNamespace(post=lambda *args, **kwargs: Response())
        with self.assertRaisesRegex(RuntimeError, "API key"):
            await client.chat_stream(session, "http://localhost", "", {}, lambda _: None)
        with self.assertRaisesRegex(RuntimeError, "API key"):
            await agent.chat_turn(session, "http://localhost", "", {})

    async def test_timeout_while_reading_response_is_actionable(self):
        session = SimpleNamespace(post=lambda *args, **kwargs: Response())
        with self.assertRaisesRegex(TimeoutError, "timeout_seconds"):
            async with stream_post(session, "http://localhost/v1/chat/completions", "", {}):
                raise TimeoutError()
