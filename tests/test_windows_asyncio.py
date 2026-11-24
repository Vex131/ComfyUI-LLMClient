import socket
import hashlib
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from providers import windows_asyncio


class WindowsAsyncioBackportTests(unittest.TestCase):
    def transport(self):
        sock = Mock()
        sock.fileno.return_value = 42
        return SimpleNamespace(_called_connection_lost=False, _protocol=Mock(),
                               _sock=sock, _server=Mock())

    def test_shutdown_reset_still_completes_cleanup(self):
        transport = self.transport()
        sock, server = transport._sock, transport._server
        sock.shutdown.side_effect = ConnectionResetError(10054, "reset")
        windows_asyncio._call_connection_lost(transport, None)
        sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        sock.close.assert_called_once_with()
        server._detach.assert_called_once_with(transport)
        self.assertIsNone(transport._sock)
        self.assertIsNone(transport._server)
        self.assertTrue(transport._called_connection_lost)
        windows_asyncio._call_connection_lost(transport, None)
        sock.close.assert_called_once()

    def test_protocol_reset_is_not_suppressed(self):
        transport = self.transport()
        sock, server = transport._sock, transport._server
        transport._protocol.connection_lost.side_effect = ConnectionResetError("protocol")
        with self.assertRaisesRegex(ConnectionResetError, "protocol"):
            windows_asyncio._call_connection_lost(transport, None)
        sock.close.assert_called_once_with()
        server._detach.assert_called_once_with(transport)

    def test_unexpected_shutdown_error_is_not_suppressed(self):
        transport = self.transport()
        transport._sock.shutdown.side_effect = OSError("unexpected")
        with self.assertRaisesRegex(OSError, "unexpected"):
            windows_asyncio._call_connection_lost(transport, None)

    def test_install_only_replaces_matching_original_once(self):
        original = SimpleNamespace(__module__="asyncio.proactor_events",
                                   __code__=SimpleNamespace(co_names=windows_asyncio._ORIGINAL_NAMES,
                                                            co_code=b"original"))
        transport = type("Transport", (), {"_call_connection_lost": original})
        module = ModuleType("asyncio.proactor_events")
        module._ProactorBasePipeTransport = transport
        with patch.dict(sys.modules, {"asyncio.proactor_events": module}), \
             patch.object(windows_asyncio.sys, "platform", "win32"), \
             patch.object(windows_asyncio.sys, "version_info", (3, 13, 7)), \
             patch.object(windows_asyncio, "_ORIGINAL_CODE_SHA256", hashlib.sha256(b"original").hexdigest()):
            self.assertTrue(windows_asyncio.install())
            self.assertTrue(windows_asyncio.install())
            self.assertIs(transport._call_connection_lost, windows_asyncio._call_connection_lost)
            other_patch = Mock()
            transport._call_connection_lost = other_patch
            self.assertFalse(windows_asyncio.install())
            self.assertIs(transport._call_connection_lost, other_patch)
            changed = SimpleNamespace(__module__="asyncio.proactor_events",
                                      __code__=SimpleNamespace(co_names=windows_asyncio._ORIGINAL_NAMES,
                                                               co_code=b"different"))
            transport._call_connection_lost = changed
            self.assertFalse(windows_asyncio.install())
            self.assertIs(transport._call_connection_lost, changed)

    def test_install_skips_other_python_versions(self):
        with patch.object(windows_asyncio.sys, "platform", "win32"), \
             patch.object(windows_asyncio.sys, "version_info", (3, 14, 0)):
            self.assertFalse(windows_asyncio.install())
