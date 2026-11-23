import json
import os
import re
import time
from collections import OrderedDict
from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit

import aiohttp

from .webfetch import html_to_text

DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
TAVILY_URL = "https://api.tavily.com/search"
DEFAULT_TIMEOUT = 15
MAX_RESULTS = 6
MAX_SNIPPET = 300
MAX_CHARS = 6000
CACHE_TTL = 1800
CACHE_MAX = 128
BLOCK_COOLDOWN = 900
COOLDOWN_MAX = 1800
UNAVAILABLE_HINT = ("Configure TAVILY_API_KEY (or a SearXNG instance via SEARXNG_URL); for now use "
                    "web_fetch with a known URL or retry later.")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

_RESULT_ANCHOR = re.compile(
    r"<a(?=[^>]*class=\"result__a\")[^>]*?href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.DOTALL)
_SNIPPET_ANCHOR = re.compile(
    r"<(?:a|td)[^>]*class=\"result(?:__|-)snippet[^\"]*\"[^>]*>(.*?)</(?:a|td)>", re.DOTALL)
_LITE_LINK = re.compile(r"<a(?=[^>]*class=\"result-link\")[^>]*?href=\"([^\"]+)\"[^>]*>(.*?)</a>",
                        re.DOTALL)

_BLOCK_MARKERS = ("anomaly-modal", "anomaly_cc")

_engine_state = {}
_result_cache = OrderedDict()


def _reset_resilience_state() -> None:
    _engine_state.clear()
    _result_cache.clear()


def _engine(name: str) -> dict:
    state = _engine_state.get(name)
    if state is None:
        state = {"fail_count": 0, "cooldown_until": 0.0, "reason": ""}
        _engine_state[name] = state
    return state


def _mark_ok(name: str) -> None:
    state = _engine(name)
    state["fail_count"] = 0
    state["cooldown_until"] = 0.0
    state["reason"] = ""


def _mark_fail(name: str, now: float, reason: str) -> None:
    state = _engine(name)
    state["fail_count"] += 1
    state["cooldown_until"] = now + min(COOLDOWN_MAX, 60 * 2 ** (state["fail_count"] - 1))
    state["reason"] = reason


def _mark_block(name: str, now: float, reason: str) -> None:
    state = _engine(name)
    state["fail_count"] += 1
    state["cooldown_until"] = now + BLOCK_COOLDOWN
    state["reason"] = reason


def _cooldown_line(name: str, now: float) -> str:
    state = _engine(name)
    remaining = max(0, int(state["cooldown_until"] - now))
    return "{}: cooling down, retry in {}s (last error: {})".format(name, remaining, state["reason"])


def _credstore_secret(name: str) -> str:
    try:
        from . import credstore
        return credstore.get_secret(name) or ""
    except Exception:
        return ""


def _cache_get(query_key: str, now: float):
    entry = _result_cache.get(query_key)
    if entry is None:
        return None
    expiry, text = entry
    if expiry <= now:
        del _result_cache[query_key]
        return None
    _result_cache.move_to_end(query_key)
    return text


def _cache_put(query_key: str, now: float, text: str) -> None:
    _result_cache[query_key] = (now + CACHE_TTL, text)
    _result_cache.move_to_end(query_key)
    while len(_result_cache) > CACHE_MAX:
        _result_cache.popitem(last=False)


class _EngineError(RuntimeError):
    pass


class _BlockError(_EngineError):
    pass


def _resolve_ddg_url(href: str) -> str:
    href = unquote(href)
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if "duckduckgo.com" in parts.netloc and "/l/" in parts.path:
        target = parse_qs(parts.query).get("uddg", [""])[0]
        if target:
            return target
    return href


def _parse_results(markup: str) -> list:
    titles = [(href, html_to_text(title)) for href, title in _RESULT_ANCHOR.findall(markup)]
    if not titles:
        titles = [(href, html_to_text(title)) for href, title in _LITE_LINK.findall(markup)
                  if href.startswith("http")]
    snippets = [html_to_text(s) for s in _SNIPPET_ANCHOR.findall(markup)]
    results = []
    for i, (href, title) in enumerate(titles[:MAX_RESULTS]):
        if not title:
            continue
        entry = {"title": title, "url": _resolve_ddg_url(href)}
        if i < len(snippets) and snippets[i]:
            snippet = snippets[i]
            entry["snippet"] = snippet[:MAX_SNIPPET] + ("..." if len(snippet) > MAX_SNIPPET else "")
        results.append(entry)
    return results


def _format_results(results: list) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        lines.append("{}. {}".format(i, r["title"]))
        lines.append("   {}".format(r["url"]))
        if r.get("snippet"):
            lines.append("   {}".format(r["snippet"]))
    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n...[truncated]"
    return text


def _map_json_results(data) -> list:
    results = []
    items = data.get("results") if isinstance(data, dict) else None
    for item in (items or [])[:MAX_RESULTS]:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title or not url:
            continue
        entry = {"title": title, "url": url}
        snippet = (item.get("content") or "").strip()[:MAX_SNIPPET]
        if snippet:
            entry["snippet"] = snippet
        results.append(entry)
    return results


def _looks_blocked(markup: str) -> bool:
    lowered = markup.lower()
    return any(marker in lowered for marker in _BLOCK_MARKERS)


async def _fetch_markup(session, url: str, query: str, timeout: int) -> str:
    try:
        async with session.get(url, params={"q": query}, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=timeout),
                               allow_redirects=True) as resp:
            if resp.status in (202, 403):
                raise _BlockError("HTTP {} from DuckDuckGo (anomaly block)".format(resp.status))
            if resp.status != 200:
                raise _EngineError("HTTP {} from search provider".format(resp.status))
            markup = (await resp.content.read(512 * 1024)).decode("utf-8", errors="replace")
    except TimeoutError:
        raise _EngineError("timeout after {}s".format(timeout))
    except aiohttp.ClientError as exc:
        raise _EngineError("web_search failed: {}".format(exc))
    if _looks_blocked(markup):
        raise _BlockError("anomaly challenge markup from DuckDuckGo")
    return markup


async def _tavily_attempt(session, api_key: str, query: str, timeout: int) -> list:
    try:
        async with session.post(TAVILY_URL,
                                json={"query": query, "max_results": MAX_RESULTS,
                                      "search_depth": "basic", "include_answer": False},
                                headers={"Authorization": "Bearer " + api_key},
                                timeout=aiohttp.ClientTimeout(total=timeout),
                                allow_redirects=False) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                raise _EngineError("Tavily redirect (HTTP {}) not followed".format(resp.status))
            body = (await resp.content.read(512 * 1024)).decode("utf-8", errors="replace")
            if resp.status != 200:
                if resp.status in (429, 432) or "usage limit" in body.lower():
                    raise _EngineError("Tavily usage limit (HTTP {})".format(resp.status))
                raise _EngineError("HTTP {} from Tavily".format(resp.status))
    except TimeoutError:
        raise _EngineError("Tavily request timed out after {}s".format(timeout))
    except aiohttp.ClientError as exc:
        raise _EngineError("tavily request failed: {}".format(exc))
    try:
        data = json.loads(body)
    except ValueError:
        raise _EngineError("invalid JSON from Tavily")
    results = _map_json_results(data)
    if not results:
        raise _EngineError("no results from Tavily")
    return results


async def _searxng_attempt(session, searxng_url: str, query: str, timeout: int) -> list:
    root = searxng_url.rstrip("/")
    try:
        async with session.get(root + "/search", params={"q": query, "format": "json"},
                               timeout=aiohttp.ClientTimeout(total=timeout),
                               allow_redirects=True) as resp:
            if resp.status == 403:
                raise _BlockError("HTTP 403 from SearXNG: JSON output is disabled, enable "
                                  "format: json in the instance's settings.yml")
            if resp.status != 200:
                raise _EngineError("HTTP {} from SearXNG".format(resp.status))
            body = (await resp.content.read(512 * 1024)).decode("utf-8", errors="replace")
    except TimeoutError:
        raise _EngineError("SearXNG request timed out after {}s".format(timeout))
    except aiohttp.ClientError as exc:
        raise _EngineError("searxng request failed: {}".format(exc))
    try:
        data = json.loads(body)
    except ValueError:
        raise _EngineError("invalid JSON from SearXNG (is format: json enabled in settings.yml?)")
    results = _map_json_results(data)
    if not results:
        raise _EngineError("no results from SearXNG")
    return results


async def _attempt_ddg(session, query: str, base_url: str, lite_url: str, timeout: int,
                       now: float) -> tuple:
    errors = []
    block_seen = False
    results = []
    if base_url:
        try:
            results = _parse_results(await _fetch_markup(session, base_url, query, timeout))
        except _BlockError as exc:
            block_seen = True
            errors.append(str(exc))
        except _EngineError as exc:
            errors.append(str(exc))
    if not results and lite_url:
        try:
            results = _parse_results(await _fetch_markup(session, lite_url, query, timeout))
        except _BlockError as exc:
            block_seen = True
            errors.append(str(exc))
        except _EngineError as exc:
            errors.append(str(exc))
    if block_seen:
        _mark_block("duckduckgo", now, "; ".join(errors))
    elif errors:
        _mark_fail("duckduckgo", now, "; ".join(errors))
    else:
        _mark_ok("duckduckgo")
    return results, errors


async def search(session, query: str, base_url: str = DDG_HTML_URL, lite_url: str = DDG_LITE_URL,
                 timeout: int = DEFAULT_TIMEOUT, tavily_api_key: Optional[str] = None,
                 searxng_url: Optional[str] = None, clock=time.monotonic) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("web_search requires a non-empty query")
    query = query.strip()
    now = clock()
    cache_key = query.lower()
    cached = _cache_get(cache_key, now)
    if cached is not None:
        return cached

    tkey = (tavily_api_key or os.environ.get("TAVILY_API_KEY", "")
            or _credstore_secret("search.tavily_api_key")).strip()
    surl = (searxng_url or _credstore_secret("search.searxng_url")
            or os.environ.get("SEARXNG_URL", "")).strip()

    failures = []

    if tkey:
        state = _engine("tavily")
        if state["cooldown_until"] > now:
            failures.append(_cooldown_line("tavily", now))
        else:
            try:
                results = await _tavily_attempt(session, tkey, query, timeout)
            except _EngineError as exc:
                _mark_fail("tavily", now, str(exc))
                failures.append("tavily: {}".format(exc))
            else:
                _mark_ok("tavily")
                text = _format_results(results)
                _cache_put(cache_key, now, text)
                return text

    if surl:
        state = _engine("searxng")
        if state["cooldown_until"] > now:
            failures.append(_cooldown_line("searxng", now))
        else:
            try:
                results = await _searxng_attempt(session, surl, query, timeout)
            except _BlockError as exc:
                _mark_block("searxng", now, str(exc))
                failures.append("searxng: {}".format(exc))
            except _EngineError as exc:
                _mark_fail("searxng", now, str(exc))
                failures.append("searxng: {}".format(exc))
            else:
                _mark_ok("searxng")
                text = _format_results(results)
                _cache_put(cache_key, now, text)
                return text

    state = _engine("duckduckgo")
    if state["cooldown_until"] > now:
        failures.append(_cooldown_line("duckduckgo", now))
    else:
        results, errors = await _attempt_ddg(session, query, base_url, lite_url, timeout, now)
        if results:
            text = _format_results(results)
            _cache_put(cache_key, now, text)
            return text
        if not errors:
            return "NO RESULTS for {!r} (the search endpoint may be blocking or changing its markup).".format(query)
        failures.append("duckduckgo: {}".format("; ".join(errors)))

    return "SEARCH UNAVAILABLE: {}. {}".format("\n".join(failures), UNAVAILABLE_HINT)
