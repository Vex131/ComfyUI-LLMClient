import re
from urllib.parse import parse_qs, unquote, urlsplit

import aiohttp

from .webfetch import html_to_text

DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
DEFAULT_TIMEOUT = 15
MAX_RESULTS = 6
MAX_SNIPPET = 300
MAX_CHARS = 6000
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


async def _fetch_markup(session, base_url: str, query: str, timeout: int) -> str:
    try:
        async with session.get(base_url, params={"q": query}, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=timeout),
                               allow_redirects=True) as resp:
            if resp.status != 200:
                raise RuntimeError("HTTP {} from search provider".format(resp.status))
            return (await resp.content.read(512 * 1024)).decode("utf-8", errors="replace")
    except aiohttp.ClientError as exc:
        raise RuntimeError("web_search failed: {}".format(exc))


async def search(session, query: str, base_url: str = DDG_HTML_URL, lite_url: str = DDG_LITE_URL,
                 timeout: int = DEFAULT_TIMEOUT) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("web_search requires a non-empty query")
    query = query.strip()
    markup = await _fetch_markup(session, base_url, query, timeout)
    results = _parse_results(markup)
    if not results and lite_url:
        markup = await _fetch_markup(session, lite_url, query, timeout)
        results = _parse_results(markup)
    if not results:
        return "NO RESULTS for {!r} (the search endpoint may be blocking or changing its markup).".format(query)
    return _format_results(results)
