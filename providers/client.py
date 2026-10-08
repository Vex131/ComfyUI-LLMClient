import json
from urllib.parse import urlsplit

import aiohttp

from .profiles import CAPABILITIES


def normalize_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    if "://" not in root:
        root = "http://" + root
    if not root.endswith("/v1"):
        root += "/v1"
    return root


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


async def _reachable(session, url: str, headers: dict) -> bool:
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as resp:
            return resp.status == 200
    except aiohttp.ClientError:
        return False


async def probe(session, base_url: str, api_key: str) -> dict:
    root = normalize_root(base_url)
    origin = origin_root(base_url)
    headers = _headers(api_key)
    try:
        async with session.get(root + "/models", headers=headers) as resp:
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
        models.append({"id": model_id, "vision": vision, "reasoning": None})

    profile = "generic"
    context = None
    if await _reachable(session, origin + "/props", headers):
        profile = "llamacpp"
        try:
            async with session.get(origin + "/props", headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    props = await resp.json()
                    context = props.get("total_n_ctx") or props.get("slot_n_ctx")
        except (aiohttp.ClientError, ValueError):
            pass
    elif await _reachable(session, origin + "/is_sleeping", headers):
        profile = "vllm"
    return {"ok": True, "profile": profile, "models": models, "context": context, "error": None}


def build_chat_payload(model: str, messages: list, sampling: dict = None, seed: int = -1) -> dict:
    payload = {"model": model, "messages": messages}
    if sampling:
        if sampling["temperature"] >= 0:
            payload["temperature"] = sampling["temperature"]
        if sampling["top_p"] >= 0:
            payload["top_p"] = sampling["top_p"]
        if sampling["top_k"] >= 0:
            payload["top_k"] = sampling["top_k"]
        if sampling["max_tokens"] >= 0:
            payload["max_tokens"] = sampling["max_tokens"]
        if sampling["reasoning_effort"]:
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
                            headers=_headers(api_key)) as resp:
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
    async with session.post(url, headers=headers, json=json_body) as resp:
        if resp.status != 200:
            raise RuntimeError("HTTP {} from provider: {}".format(resp.status, (await resp.text())[:300]))


async def unload_command(session, base_url: str, api_key: str, profile: str, mode: str, model_id: str) -> str:
    capability = CAPABILITIES.get(profile, {}).get("unload")
    if capability is None:
        raise ValueError("Provider '{}' does not expose an unload API".format(profile))

    origin = origin_root(base_url)
    headers = _headers(api_key)

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
        async with session.get(origin + "/models", headers=headers) as resp:
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
