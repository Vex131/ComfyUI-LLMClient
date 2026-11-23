import inspect
import json
import re

from .client import _headers, build_chat_payload, normalize_root

TOOL_SCHEMAS = {
    "search_documents": {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "Search the configured local documents semantically. Returns relevant passages and sources.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Question or search query."}},
                "required": ["query"],
            },
        },
    },
    "list_local_models": {
        "type": "function",
        "function": {
            "name": "list_local_models",
            "description": "List model files installed in this ComfyUI instance: checkpoints, "
                           "diffusion models, LoRAs, embeddings, VAEs, CLIPs, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "'all' or one of: checkpoints, diffusion_models, loras, "
                                       "embeddings, clip, text_encoders, vae, controlnet, upscale_models",
                        "default": "all",
                    },
                },
            },
        },
    },
    "get_model_details": {
        "type": "function",
        "function": {
            "name": "get_model_details",
            "description": "Full details for one installed model file: embedded safetensors metadata "
                           "(description cards, trigger words, training tags) and sidecar .txt/.md/.json "
                           "card files. Call after list_local_models so category and name match exactly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Category as reported by list_local_models, or 'all'."},
                    "name": {"type": "string", "description": "File name exactly as reported by list_local_models."},
                },
                "required": ["name"],
            },
        },
    },
    "web_fetch": {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a web page (http/https) as plain text. Use for model cards, trigger "
                           "words or documentation that is not available locally.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Absolute http(s) URL."}},
                "required": ["url"],
            },
        },
    },
    "web_search": {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web (Tavily/SearXNG/DuckDuckGo with automatic failover and "
                           "caching). Returns titles, URLs and snippets. If results are unavailable, "
                           "use web_fetch with a known URL instead.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query."}},
                "required": ["query"],
            },
        },
    },
}

REACT_SYSTEM = (
    "Native tool-calling is not available on this provider. Use a plain-text tool protocol instead. "
    'To call a tool, reply with ONLY a JSON object: {"tool": "<tool_name>", "args": {...}}. '
    'When finished, reply with ONLY: {"final": "<complete final answer>"}. '
    "Never wrap these objects in prose or extra commentary."
)

MAX_TOOL_RESULT = 24000


def tool_schemas(names) -> list:
    out = []
    for name in names:
        out.append(TOOL_SCHEMAS.get(name, {
            "type": "function",
            "function": {"name": name, "description": "Tool: {}".format(name),
                         "parameters": {"type": "object"}},
        }))
    return out


def _balanced_objects(text: str) -> list:
    objects = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                objects.append(text[start:i + 1])
                start = -1
    return objects


def parse_react(text: str):
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text or "", re.DOTALL)
    for candidate in fenced + _balanced_objects(text or ""):
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict) and ("final" in obj or "tool" in obj):
            return obj
    return None


async def chat_turn(session, base_url: str, api_key: str, payload: dict, on_chunk=None,
                    should_interrupt=None) -> tuple[str, str, list]:
    root = normalize_root(base_url)
    text = ""
    reasoning = ""
    calls: dict = {}
    async with session.post(root + "/chat/completions", json=dict(payload, stream=True),
                            headers=_headers(api_key), allow_redirects=False) as resp:
        if resp.status in (301, 302, 303, 307, 308):
            raise RuntimeError("HTTP {} redirect from provider not followed for credentialed "
                               "requests".format(resp.status))
        if resp.status != 200:
            raise RuntimeError("HTTP {} from provider: {}".format(resp.status, (await resp.text())[:500]))
        async for raw_line in resp.content:
            if should_interrupt is not None and should_interrupt():
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
                if on_chunk is not None:
                    on_chunk(len(text))
            thought = delta.get("reasoning_content") or delta.get("reasoning")
            if thought:
                reasoning += thought
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
    return text, reasoning, [calls[i] for i in sorted(calls)]


async def _call_tool(impls, name: str, args, session) -> str:
    impl = impls.get(name)
    if impl is None:
        return "UNKNOWN TOOL: {}. Available: {}".format(name, ", ".join(sorted(impls)) or "none")
    try:
        if inspect.iscoroutinefunction(impl):
            result = await impl(args, session)
        else:
            result = impl(args, session)
    except Exception as exc:
        return "TOOL ERROR: {}: {}".format(type(exc).__name__, exc)
    if not isinstance(result, str):
        result = json.dumps(result)
    return result[:MAX_TOOL_RESULT]


async def run_loop(session, base_url: str, api_key: str, model: str, messages: list,
                   sampling=None, seed: int = -1, tool_impls=None,
                   max_iterations: int = 8, tool_mode: str = "auto",
                   on_chunk=None, should_interrupt=None) -> dict:
    tool_impls = tool_impls or {}
    react = tool_mode == "json" and bool(tool_impls)
    tools = tool_schemas(tool_impls) if (tool_impls and not react) else []
    if react:
        messages.append({"role": "system", "content": REACT_SYSTEM})
    final = None
    last_text = ""
    reasoning_total = ""
    fell_back = False
    iterations = 0
    for iteration in range(1, max(1, max_iterations) + 1):
        iterations = iteration
        if should_interrupt is not None and should_interrupt():
            break
        payload: dict = dict(build_chat_payload(model, messages, sampling or {}, seed))
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        try:
            text, reasoning, calls = await chat_turn(session, base_url, api_key, payload,
                                                     on_chunk=on_chunk, should_interrupt=should_interrupt)
        except RuntimeError as exc:
            if tools and tool_mode == "auto" and not fell_back and "tool" in str(exc).lower():
                fell_back = True
                tools = []
                react = True
                messages.append({"role": "system", "content": REACT_SYSTEM})
                continue
            raise
        if reasoning:
            reasoning_total += (reasoning_total and "\n" or "") + reasoning
        assistant: dict = {"role": "assistant"}
        if text:
            assistant["content"] = text
        elif calls:
            assistant["content"] = None
        if calls:
            assistant["tool_calls"] = [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in calls
            ]
        messages.append(assistant)
        if text:
            last_text = text
        if react:
            obj = parse_react(text)
            if obj and obj.get("final") is not None:
                final = str(obj["final"])
                break
            if obj and obj.get("tool"):
                result = await _call_tool(tool_impls, str(obj["tool"]), obj.get("args") or {}, session)
                messages.append({"role": "user", "content": "TOOL_RESULT({}): {}".format(obj["tool"], result)})
                continue
            final = text
            break
        if not calls:
            final = text
            break
        for c in calls:
            try:
                args = json.loads(c["arguments"] or "{}")
            except ValueError:
                args = {}
            result = await _call_tool(tool_impls, c["name"], args, session)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
    else:
        if should_interrupt is None or not should_interrupt():
            messages.append({"role": "user",
                             "content": "Iteration budget exhausted. Produce your complete final "
                                        "answer now, without tool calls."})
            closing = dict(build_chat_payload(model, messages, sampling or {}, seed))
            try:
                text, reasoning, _ = await chat_turn(session, base_url, api_key, closing,
                                                     on_chunk=on_chunk, should_interrupt=should_interrupt)
            except RuntimeError:
                text = ""
            else:
                if reasoning:
                    reasoning_total += (reasoning_total and "\n" or "") + reasoning
                assistant = {"role": "assistant"}
                if text:
                    assistant["content"] = text
                messages.append(assistant)
            final = ""
            if text:
                final = text
                if react:
                    obj = parse_react(text)
                    if obj and obj.get("final") is not None:
                        final = str(obj["final"])
            if not final:
                final = "[max iterations reached]\n" + (last_text or "")
    if final is None:
        final = last_text
    return {
        "final": final,
        "reasoning": reasoning_total,
        "messages": messages,
        "iterations": iterations,
        "react": react,
    }
