import base64
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from ctypes import wintypes
from datetime import datetime, timezone
from typing import Any

BACKEND = "file"
FALLBACK_REASON = None
STORE_NOTICES: list = []


def _record_store_notice(message: str) -> None:
    if message not in STORE_NOTICES:
        STORE_NOTICES.append(message)

_LOCK = threading.RLock()
_ID_RE = re.compile(r"^[A-Za-z0-9_.:/<>[\]-]{1,300}$")
_ID_ERROR = "invalid secret id: must be 1-300 characters of [A-Za-z0-9_.:/<>[]-]"
_TARGET_PREFIX = "ComfyUI-LLMClient/"
# Without O_BINARY the Windows CRT opens text mode and os.write rewrites every
# 0x0A byte to 0x0D 0x0A, corrupting DPAPI blobs and any binary payload.
_BINARY_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)


def _default_file_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "comfyui-llmclient")


def _local_appdata_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "ComfyUI-LLMClient")


if os.name == "nt":
    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Type", wintypes.DWORD),
            ("Size", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.c_void_p),
            ("Persist", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("AttributeCount", wintypes.DWORD),
            ("TargetAliasName", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    _ADVAPI32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _ADVAPI32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    _ADVAPI32.CredWriteW.restype = wintypes.BOOL
    _ADVAPI32.CredReadW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
    ]
    _ADVAPI32.CredReadW.restype = wintypes.BOOL
    _ADVAPI32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _ADVAPI32.CredDeleteW.restype = wintypes.BOOL
    _ADVAPI32.CredEnumerateW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))),
    ]
    _ADVAPI32.CredEnumerateW.restype = wintypes.BOOL
    _ADVAPI32.CredFree.argtypes = [ctypes.c_void_p]

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _CRYPT32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _CRYPT32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _CRYPT32.CryptProtectData.restype = wintypes.BOOL
    _CRYPT32.CryptUnprotectData.argtypes = _CRYPT32.CryptProtectData.argtypes
    _CRYPT32.CryptUnprotectData.restype = wintypes.BOOL
    _CRYPT32.CryptMemFree.argtypes = [ctypes.c_void_p]

    def _dpapi_protect(data: bytes) -> bytes:
        buf = (ctypes.c_char * len(data)).from_buffer_copy(data)
        blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()
        if not _CRYPT32.CryptProtectData(ctypes.byref(blob_in), "ComfyUI-LLMClient",
                                         None, None, None, 0, ctypes.byref(blob_out)):
            raise OSError("CryptProtectData failed: {}".format(ctypes.get_last_error()))
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _CRYPT32.CryptMemFree(blob_out.pbData)

    def _dpapi_unprotect(data: bytes) -> bytes:
        buf = (ctypes.c_char * len(data)).from_buffer_copy(data)
        blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()
        if not _CRYPT32.CryptUnprotectData(ctypes.byref(blob_in), "ComfyUI-LLMClient",
                                           None, None, None, 0, ctypes.byref(blob_out)):
            raise OSError("CryptUnprotectData failed: {}".format(ctypes.get_last_error()))
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _CRYPT32.CryptMemFree(blob_out.pbData)


class _CredManStore:
    backend = "win-credential-manager"

    def start(self):
        pass

    def _target(self, secret_id: str) -> str:
        return _TARGET_PREFIX + secret_id

    def set_secret(self, secret_id: str, value: str):
        raw = value.encode("utf-16-le")
        buf = (ctypes.c_char * len(raw)).from_buffer_copy(raw)
        cred = _CREDENTIALW()
        cred.Type = _CRED_TYPE_GENERIC
        cred.Size = ctypes.sizeof(_CREDENTIALW)
        cred.TargetName = self._target(secret_id)
        cred.CredentialBlobSize = len(raw)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.c_void_p)
        cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
        if not _ADVAPI32.CredWriteW(ctypes.byref(cred), 0):
            raise OSError("CredWriteW failed: {}".format(ctypes.get_last_error()))

    def get_secret(self, secret_id: str):
        out = ctypes.POINTER(_CREDENTIALW)()
        if not _ADVAPI32.CredReadW(self._target(secret_id), _CRED_TYPE_GENERIC, 0, ctypes.byref(out)):
            if ctypes.get_last_error() == _ERROR_NOT_FOUND:
                return None
            raise OSError("CredReadW failed: {}".format(ctypes.get_last_error()))
        try:
            return ctypes.string_at(out.contents.CredentialBlob,
                                    out.contents.CredentialBlobSize).decode("utf-16-le")
        finally:
            _ADVAPI32.CredFree(out)

    def delete_secret(self, secret_id: str) -> bool:
        if not _ADVAPI32.CredDeleteW(self._target(secret_id), _CRED_TYPE_GENERIC, 0):
            if ctypes.get_last_error() == _ERROR_NOT_FOUND:
                return False
            raise OSError("CredDeleteW failed: {}".format(ctypes.get_last_error()))
        return True

    def list_ids(self, prefix: str) -> list:
        count = wintypes.DWORD()
        array = ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))()
        if not _ADVAPI32.CredEnumerateW(None, 0, ctypes.byref(count), ctypes.byref(array)):
            if ctypes.get_last_error() == _ERROR_NOT_FOUND:
                return []
            raise OSError("CredEnumerateW failed: {}".format(ctypes.get_last_error()))
        ids = []
        try:
            for i in range(count.value):
                target = array[i].contents.TargetName or ""
                if target.startswith(_TARGET_PREFIX):
                    secret_id = target[len(_TARGET_PREFIX):]
                    if secret_id.startswith(prefix):
                        ids.append(secret_id)
        finally:
            _ADVAPI32.CredFree(array)
        return ids


class _FileStore:
    def __init__(self, dir_path: str, backend_name: str, dpapi: bool = False):
        self._dir = dir_path
        self._dpapi = dpapi
        self.backend = backend_name
        self._path = os.path.join(dir_path, "secrets.bin" if dpapi else "secrets.json")

    def start(self):
        os.makedirs(self._dir, exist_ok=True)

    def _load_map(self) -> dict:
        try:
            with open(self._path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return {}
        if not raw:
            return {}
        try:
            if self._dpapi:
                raw = _dpapi_unprotect(raw)
            data = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            self._backup_corrupt()
            return {}
        return data if isinstance(data, dict) else {}

    def _backup_corrupt(self):
        # Move the unreadable store aside instead of letting the next write
        # overwrite it, so the bytes can still be recovered manually.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        backup_path = "{}.{}.corrupt".format(self._path, stamp)
        try:
            os.replace(self._path, backup_path)
        except OSError:
            return
        _record_store_notice("{}: unreadable store file preserved as {}".format(
            self.backend, os.path.basename(backup_path)))

    def _save_map(self, store_map: dict):
        raw = json.dumps(store_map, sort_keys=True).encode("utf-8")
        if self._dpapi:
            raw = _dpapi_protect(raw)
        tmp_path = "{}.{}.tmp".format(self._path, uuid.uuid4().hex[:8])
        fd = os.open(tmp_path, _BINARY_FLAGS, 0o600)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)
        os.replace(tmp_path, self._path)

    def set_secret(self, secret_id: str, value: str):
        store_map = self._load_map()
        store_map[secret_id] = value
        self._save_map(store_map)

    def get_secret(self, secret_id: str):
        value = self._load_map().get(secret_id)
        return value if isinstance(value, str) and value else None

    def delete_secret(self, secret_id: str) -> bool:
        store_map = self._load_map()
        if secret_id not in store_map:
            return False
        del store_map[secret_id]
        self._save_map(store_map)
        return True

    def list_ids(self, prefix: str) -> list:
        return sorted(key for key in self._load_map() if key.startswith(prefix))


class _IndexFileMixin:
    _index_path: str

    def _index_read(self) -> list:
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [item for item in data if isinstance(item, str)] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _index_write(self, ids):
        os.makedirs(os.path.dirname(self._index_path), exist_ok=True)
        tmp_path = "{}.{}.tmp".format(self._index_path, uuid.uuid4().hex[:8])
        fd = os.open(tmp_path, _BINARY_FLAGS, 0o600)
        try:
            os.write(fd, json.dumps(sorted(set(ids))).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp_path, self._index_path)

    def _index_add(self, secret_id: str):
        ids = set(self._index_read())
        ids.add(secret_id)
        self._index_write(ids)

    def _index_remove(self, secret_id: str):
        ids = set(self._index_read())
        ids.discard(secret_id)
        self._index_write(ids)


class _MacKeychainStore(_IndexFileMixin):
    backend = "macos-keychain"

    def __init__(self, index_path: str):
        self._index_path = index_path
        self._user = os.environ.get("USER") or os.environ.get("LOGNAME") or "comfyui-llmclient"

    def start(self):
        os.makedirs(os.path.dirname(self._index_path), exist_ok=True)

    def _service(self, secret_id: str) -> str:
        return _TARGET_PREFIX + secret_id

    def set_secret(self, secret_id: str, value: str):
        service = self._service(secret_id)
        subprocess.run(["security", "delete-generic-password", "-s", service],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = subprocess.run(["security", "add-generic-password", "-a", self._user,
                                 "-s", service, "-w", value],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError("security add-generic-password failed: " + (result.stderr or "").strip())
        self._index_add(secret_id)

    def get_secret(self, secret_id: str):
        result = subprocess.run(["security", "find-generic-password", "-s",
                                 self._service(secret_id), "-w"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            return None
        return result.stdout.strip("\n") or None

    def delete_secret(self, secret_id: str) -> bool:
        result = subprocess.run(["security", "delete-generic-password", "-s",
                                 self._service(secret_id)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._index_remove(secret_id)
        return result.returncode == 0

    def list_ids(self, prefix: str) -> list:
        return sorted(item for item in self._index_read() if item.startswith(prefix))


class _SecretToolStore(_IndexFileMixin):
    backend = "linux-secret-service"

    def __init__(self, index_path: str):
        self._index_path = index_path

    def start(self):
        if shutil.which("secret-tool") is None:
            raise RuntimeError("secret-tool is not available")

    def _attrs(self, secret_id: str) -> list:
        return ["service", _TARGET_PREFIX.rstrip("/"), "id", secret_id]

    def set_secret(self, secret_id: str, value: str):
        result = subprocess.run(["secret-tool", "store", "--label=ComfyUI-LLMClient"] + self._attrs(secret_id),
                                input=value.encode("utf-8"),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise RuntimeError("secret-tool store failed: " + (result.stderr or b"").decode("utf-8", "replace").strip())
        self._index_add(secret_id)

    def get_secret(self, secret_id: str):
        result = subprocess.run(["secret-tool", "lookup"] + self._attrs(secret_id),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8") or None

    def delete_secret(self, secret_id: str) -> bool:
        result = subprocess.run(["secret-tool", "clear"] + self._attrs(secret_id),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._index_remove(secret_id)
        return result.returncode == 0

    def list_ids(self, prefix: str) -> list:
        return sorted(item for item in self._index_read() if item.startswith(prefix))


def _health_check(store) -> None:
    probe_id = "health." + uuid.uuid4().hex
    store.set_secret(probe_id, "health-probe-value")
    try:
        if store.get_secret(probe_id) != "health-probe-value":
            raise RuntimeError("health probe readback mismatch")
    finally:
        store.delete_secret(probe_id)


def _file_store_from_env() -> _FileStore:
    return _FileStore(os.environ.get("COMFYUI_LLMCLIENT_FILE_DIR") or _default_file_dir(), "file")


def _init_backend():
    global BACKEND, FALLBACK_REASON, _STORE
    STORE_NOTICES.clear()
    forced = (os.environ.get("COMFYUI_LLMCLIENT_BACKEND") or "").strip().lower()
    fallback = _file_store_from_env()

    if forced == "file":
        candidates = [fallback]
    elif sys.platform == "win32":
        candidates = [_CredManStore(),
                      _FileStore(_local_appdata_dir(), "win-dpapi-file", dpapi=True),
                      fallback]
    elif sys.platform == "darwin":
        candidates = [_MacKeychainStore(os.path.join(_default_file_dir(), "ids.json")), fallback]
    else:
        candidates = [_SecretToolStore(os.path.join(_default_file_dir(), "ids.json")), fallback]

    reasons = []
    for store in candidates:
        try:
            store.start()
            _health_check(store)
        except Exception as exc:
            reasons.append("{}: {}".format(store.backend, exc))
            continue
        BACKEND = store.backend
        FALLBACK_REASON = "; ".join(reasons) if reasons else None
        _STORE = store
        return

    BACKEND = "file"
    FALLBACK_REASON = "; ".join(reasons) if reasons else "no preferred backend available"
    _STORE = fallback


_STORE: Any = None
_init_backend()


def _validate_secret_id(secret_id):
    if not isinstance(secret_id, str) or not _ID_RE.match(secret_id):
        raise ValueError(_ID_ERROR)


def set_secret(secret_id: str, value: str) -> None:
    _validate_secret_id(secret_id)
    if not isinstance(value, str) or value == "":
        raise ValueError("secret value must be a non-empty string")
    with _LOCK:
        _STORE.set_secret(secret_id, value)


def get_secret(secret_id: str):
    _validate_secret_id(secret_id)
    with _LOCK:
        return _STORE.get_secret(secret_id)


def delete_secret(secret_id: str) -> bool:
    _validate_secret_id(secret_id)
    with _LOCK:
        return _STORE.delete_secret(secret_id)


def list_ids(prefix: str) -> list:
    if not isinstance(prefix, str) or len(prefix) > 300:
        raise ValueError(_ID_ERROR)
    if prefix:
        _validate_secret_id(prefix)
    with _LOCK:
        return _STORE.list_ids(prefix)


def preview(secret_id: str) -> str:
    value = get_secret(secret_id)
    if not value:
        return ""
    if len(value) >= 10:
        return value[:5] + "…" + value[-4:]
    if len(value) > 1:
        return value[:1] + "…"
    return "…"
