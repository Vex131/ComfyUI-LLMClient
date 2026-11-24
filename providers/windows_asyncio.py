"""Narrow backport of CPython gh-160429 for Windows Python 3.13.7."""
import hashlib
import socket
import sys


# The original 3.13.7 method is checked before replacement, so another
# extension's patch or a different runtime implementation is never overwritten.
_ORIGINAL_NAMES = (
    "_called_connection_lost", "_protocol", "connection_lost", "hasattr",
    "_sock", "fileno", "shutdown", "socket", "SHUT_RDWR", "close",
    "_server", "_detach",
)
_ORIGINAL_CODE_SHA256 = "0a7ed9e15e3e513c3a8928442aa3396d64831216c36d3d15d84f057120fb296e"


def _call_connection_lost(self, exc):
    if self._called_connection_lost:
        return
    try:
        self._protocol.connection_lost(exc)
    finally:
        if hasattr(self._sock, "shutdown") and self._sock.fileno() != -1:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except ConnectionError:
                # The peer has already disconnected; finish transport cleanup.
                pass
        self._sock.close()
        self._sock = None
        server = self._server
        if server is not None:
            server._detach(self)
            self._server = None
        self._called_connection_lost = True


def install():
    """Install the upstream fix only on the known affected runtime."""
    if sys.platform != "win32" or sys.version_info[:3] != (3, 13, 7):
        return False
    from asyncio.proactor_events import _ProactorBasePipeTransport

    original = _ProactorBasePipeTransport._call_connection_lost
    if original is _call_connection_lost:
        return True
    code = getattr(original, "__code__", None)
    if (getattr(original, "__module__", None) != "asyncio.proactor_events"
            or code is None or code.co_names != _ORIGINAL_NAMES
            or hashlib.sha256(code.co_code).hexdigest() != _ORIGINAL_CODE_SHA256):
        return False
    _ProactorBasePipeTransport._call_connection_lost = _call_connection_lost
    return True
