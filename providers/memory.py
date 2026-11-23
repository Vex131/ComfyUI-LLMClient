"""Persistent conversations; session IDs are scoped to a node owner."""
import json
import math
import os
import re
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from filelock import AsyncFileLock

_IDENTIFIER = re.compile(r"^[0-9a-f]{32}:[0-9a-f]{32}$")


def _valid_timestamp(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) \
        and 0 < value < 253402300800


def session_directory():
    import folder_paths
    return Path(folder_paths.get_user_directory()) / "llmclient" / "sessions"


def parse_session(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("Choose a session in the sessions menu before enabling memory.")
    return value.split(":")


def new_session():
    return "{}:{}".format(uuid.uuid4().hex, uuid.uuid4().hex)


def _session_path(kind, identifier):
    owner, session = parse_session(identifier)
    if kind not in ("chat", "agent"):
        raise ValueError("Invalid session type.")
    return session_directory() / kind / owner / (session + ".json")


def list_sessions(kind, identifier):
    path = _session_path(kind, identifier)
    if not path.parent.is_dir():
        return []
    result = []
    for item in path.parent.glob("*.json"):
        if not re.fullmatch(r"[0-9a-f]{32}", item.stem):
            continue
        if item.with_suffix(".deleted").exists():
            continue
        try:
            stat = item.stat()
            with item.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
                continue
            created = data.get("created_at")
            if not _valid_timestamp(created):
                created = stat.st_ctime
            result.append({"id": identifier.split(":")[0] + ":" + item.stem,
                           "created_at": created, "updated_at": stat.st_mtime})
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda item: (item["updated_at"], item["id"]), reverse=True)


async def delete_session(kind, identifier, target):
    owner, _ = parse_session(identifier)
    target_owner, _ = parse_session(target)
    if owner != target_owner:
        raise ValueError("Cannot delete a session belonging to a different node.")
    path = _session_path(kind, target)
    if not path.parent.is_dir():
        raise FileNotFoundError("Session does not exist.")
    async with AsyncFileLock(str(path) + ".lock", timeout=300):
        marker = path.with_suffix(".deleted")
        if not path.exists() and not marker.exists():
            raise FileNotFoundError("Session does not exist.")
        new_marker = not marker.exists()
        marker.touch(exist_ok=True)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            if new_marker:
                marker.unlink(missing_ok=True)
            raise


def fingerprint(memory=False, seed=-1):
    if memory is False:
        return False
    if memory is None or seed in (-1, None):
        return float("nan")
    return False


@asynccontextmanager
async def conversation(kind, identifier):
    path = _session_path(kind, identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncFileLock(str(path) + ".lock", timeout=300):
        if path.with_suffix(".deleted").exists():
            raise ValueError("This session was deleted. Select or create a different session.")
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                entry = json.load(handle)
            if not isinstance(entry, dict) or not isinstance(entry.get("messages"), list):
                raise ValueError("Session file is invalid: {}".format(path))
            if not _valid_timestamp(entry.get("created_at")):
                entry["created_at"] = path.stat().st_ctime
        else:
            entry = {"created_at": time.time(), "messages": []}
        yield entry
        fd, temp_path = tempfile.mkstemp(prefix=".session-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(entry, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


def messages(entry):
    return list(entry["messages"])


def append(entry, turn):
    entry["messages"].extend(turn)
