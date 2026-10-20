import html
import re

import aiohttp

MAX_BYTES = 512 * 1024
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


def html_to_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", markup)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article|blockquote|pre)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


async def fetch_text(session, url: str, max_chars: int = 12000) -> str:
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        raise ValueError("Only http:// and https:// URLs are allowed, got: {!r}".format(url))
    async with session.get(url, timeout=DEFAULT_TIMEOUT, allow_redirects=True) as resp:
        if resp.status != 200:
            raise RuntimeError("HTTP {} from {}".format(resp.status, url))
        content_type = resp.headers.get("Content-Type", "").lower()
        data = await resp.content.read(MAX_BYTES + 1)
    truncated_bytes = len(data) > MAX_BYTES
    text = data[:MAX_BYTES].decode("utf-8", errors="replace")
    if "html" in content_type or text.lstrip()[:1] == "<":
        text = html_to_text(text)
    if truncated_bytes or len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"
    return text
