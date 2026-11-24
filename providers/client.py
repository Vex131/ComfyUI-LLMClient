import ipaddress
import json
from urllib.parse import urlsplit

import aiohttp

from . import credstore
from .profiles import CAPABILITIES

_PRIVATE_HOSTS = ("127.0.0.1", "localhost", "::1")
# Explicit allowlist instead of ipaddress.is_private: the stdlib treats
# documentation prefixes (203.0.113.0/24 etc.) as private and RFC 6598 CGNAT
# (100.64.0.0/10) as public, which is the opposite of this policy.
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def normalize_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    if "://" not in root:
        root = "http://" + root
    if not root.endswith("/v1"):
        root += "/v1"
    return root


def _host_is_private(url: str) -> bool:
    host = (urlsplit(url).hostname or "").strip("[]").lower()
    if host in _PRIVATE_HOSTS:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or any(addr in network for network in _PRIVATE_NETWORKS)


def transport_is_safe(base_url: str) -> bool:
    root = normalize_root(base_url)
    return urlsplit(root).scheme.lower() == "https" or _host_is_private(root)


def resolve_key(base_url: str) -> str:
    root = normalize_root(base_url)
    vault_key = credstore.get_secret("llm." + root)
    if vault_key and urlsplit(root).scheme.lower() != "https" and not _host_is_private(root):
        raise ValueError(
            "refusing to send credentials over plaintext HTTP to a public host: {}".format(root))
    return vault_key or ""


def origin_root(base_url: str) -> str:
    if "://" not in base_url:
        base_url = "http://" + base_url
    parts = urlsplit(base_url)
    return "{}://{}".format(parts.scheme, parts.netloc)


def _headers(api_key: str) -> dict:
    headers = {}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    return headers


async def _get_json(session, url: str, headers: dict):
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=3),
                               allow_redirects=False) as resp:
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, ValueError, TimeoutError):
        return None


async def probe(session, base_url: str, api_key: str) -> dict:
    root = normalize_root(base_url)
    origin = origin_root(base_url)
    headers = _headers(api_key)
    try:
        async with session.get(root + "/models", headers=headers, allow_redirects=False) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                raise RuntimeError("HTTP {} redirect from {} not followed for credentialed requests".format(
                    resp.status, root + "/models"))
            if resp.status != 200:
                return {"ok": False, "profile": "generic", "models": [], "context": None,
                        "error": "GET {}/models returned HTTP {}".format(root, resp.status)}
            data = await resp.json()
    except (aiohttp.ClientError, ValueError) as exc:
        return {"ok": False, "profile": "generic", "models": [], "context": None, "error": str(exc)}

    models = []
    entries = data.get("data", []) if isinstance(data, dict) else []
    for entry in entries:
        model_id = entry.get("id")
        if not model_id:
            continue
        capabilities = entry.get("capabilities") or {}
        vision = capabilities.get("multimodal")
        if vision is None:
            vision = entry.get("multimodal")
        reasoning_modes = (capabilities.get("supported_reasoning_efforts")
                           or capabilities.get("reasoning_efforts")
                           or entry.get("supported_reasoning_efforts")
                           or entry.get("reasoning_efforts"))
        if not isinstance(reasoning_modes, list) or not all(isinstance(mode, str) and mode.strip()
                                                            for mode in reasoning_modes):
            reasoning_modes = []
        models.append({"id": model_id, "vision": vision, "reasoning": None,
                       "reasoning_efforts": list(dict.fromkeys(reasoning_modes))})

    profile = "generic"
    context = None
    health = await _get_json(session, origin + "/health", headers)
    props = await _get_json(session, origin + "/props", headers)
    if isinstance(health, dict) and health.get("service") == "strata":
        profile = "strata"
        if isinstance(props, dict):
            context = props.get("total_n_ctx") or props.get("slot_n_ctx")
    elif isinstance(props, dict) and ("total_n_ctx" in props or "slot_n_ctx" in props):
        profile = "llamacpp"
        context = props.get("total_n_ctx") or props.get("slot_n_ctx")
    elif isinstance(await _get_json(session, origin + "/is_sleeping", headers), bool):
        profile = "vllm"
    return {"ok": True, "profile": profile, "models": models, "context": context, "error": None}


def build_chat_payload(model: str, messages: list, sampling: dict = None, seed: int = -1) -> dict:
    payload = {"model": model, "messages": messages}
    if sampling:
        if sampling.get("temperature", -1) >= 0:
            payload["temperature"] = sampling["temperature"]
        if sampling.get("top_p", -1) >= 0:
            payload["top_p"] = sampling["top_p"]
        if sampling.get("top_k", -1) >= 0:
            payload["top_k"] = sampling["top_k"]
        if sampling.get("max_tokens", -1) >= 0:
            payload["max_tokens"] = sampling["max_tokens"]
        if sampling.get("reasoning_effort"):
            payload["reasoning_effort"] = sampling["reasoning_effort"]
    if seed >= 0:
        payload["seed"] = seed
    return payload


async def chat_stream(session, base_url: str, api_key: str, payload: dict, on_chunk,
                      should_interrupt=None) -> tuple[str, str]:
    root = normalize_root(base_url)
    text = ""
    reasoning = ""
    async with session.post(root + "/chat/completions", json=dict(payload, stream=True),
                            headers=_headers(api_key), allow_redirects=False) as resp:
        if resp.status in (301, 302, 303, 307, 308):
            raise RuntimeError("HTTP {} redirect from {} not followed for credentialed requests".format(
                resp.status, root + "/chat/completions"))
        if resp.status != 200:
            raise RuntimeError("HTTP {} from provider: {}".format(resp.status, (await resp.text())[:500]))
        async for raw_line in resp.content:
            if should_interrupt is not None and should_interrupt():
                # Closing the response drops the connection so the provider
                # aborts generation instead of decoding to the end of context.
                resp.close()
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            choices = chunk.get("choices") or []
            delta = choices[0].get("delta", {}) if choices else {}
            content = delta.get("content")
            if content:
                text += content
            thought = delta.get("reasoning_content") or delta.get("reasoning")
            if thought:
                reasoning += thought
            on_chunk(len(text))
    return text, reasoning


async def _post(session, url: str, headers: dict, json_body: dict = None):
    async with session.post(url, headers=headers, json=json_body, allow_redirects=False) as resp:
        if resp.status in (301, 302, 303, 307, 308):
            raise RuntimeError("HTTP {} redirect from {} not followed for credentialed requests".format(
                resp.status, url))
        if resp.status != 200:
            raise RuntimeError("HTTP {} from provider: {}".format(resp.status, (await resp.text())[:300]))


async def unload_command(session, base_url: str, api_key: str, profile: str, mode: str, model_id: str) -> str:
    capability = CAPABILITIES.get(profile, {}).get("unload")
    if capability is None:
        raise ValueError("Provider '{}' does not expose an unload API".format(profile))

    origin = origin_root(base_url)
    headers = _headers(api_key)

    if capability == "strata":
        if mode == "offload_vram_to_ram":
            raise ValueError("Strata cannot offload weights to RAM; use release_all.")
        if mode == "wake_up":
            await _post(session, origin + "/load", headers, json_body={})
            return "Strata model loaded."
        if mode != "release_all":
            raise ValueError("Unknown unload mode: {}".format(mode))
        await _post(session, origin + "/unload", headers, json_body={})
        return "Strata model unloaded; it will load again on the next request."

    if capability == "sleep":
        if mode == "offload_vram_to_ram":
            await _post(session, origin + "/sleep?level=1", headers)
            return "vLLM sleeping (level 1): weights offloaded to RAM."
        if mode == "release_all":
            await _post(session, origin + "/sleep?level=2", headers)
            return "vLLM sleeping (level 2): weights discarded."
        await _post(session, origin + "/wake_up", headers)
        return "vLLM awake: weights restored."

    try:
        async with session.get(origin + "/models", headers=headers, allow_redirects=False) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                raise RuntimeError("HTTP {} redirect from {} not followed for credentialed requests".format(
                    resp.status, origin + "/models"))
            data = await resp.json() if resp.status == 200 else {}
    except (aiohttp.ClientError, ValueError):
        data = {}
    entries = data.get("data", []) if isinstance(data, dict) else []
    if not any("status" in entry for entry in entries):
        raise ValueError("llama.cpp server is not running in router mode (--models-dir), no unload API available")

    if mode == "wake_up":
        await _post(session, origin + "/models/load", headers, json_body={"model": model_id})
        return "llama.cpp model '{}' loaded.".format(model_id)
    await _post(session, origin + "/models/unload", headers, json_body={"model": model_id})
    return "llama.cpp model '{}' unloaded.".format(model_id)
