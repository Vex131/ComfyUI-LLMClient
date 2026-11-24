import html
import ipaddress
import re
import socket
from urllib.parse import urlsplit

import aiohttp

MAX_BYTES = 512 * 1024
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _public_ip(address):
    try:
        ip = ipaddress.ip_address(address)
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        return ip.is_global and not ip.is_multicast
    except ValueError:
        return False


class PublicResolver(aiohttp.abc.AbstractResolver):
    def __init__(self):
        self._resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        results = await self._resolver.resolve(host, port, family)
        if not results or any(not _public_ip(item["host"]) for item in results):
            raise ValueError("Web fetch only permits public internet addresses.")
        return results

    async def close(self):
        await self._resolver.close()


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


async def fetch_text(session, url: str, max_chars: int = 12000, *, allow_local=False) -> str:
    # Keep the session argument for callers; fetches use their own isolated session.
    if not isinstance(url, str):
        raise ValueError("Only public HTTP and HTTPS URLs are allowed.")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if (parts.scheme.lower() not in ("http", "https") or not host
                or parts.username is not None or parts.password is not None
                or parts.port == 0 or any(ch.isspace() for ch in url)):
            raise ValueError
    except ValueError:
        raise ValueError("Only public HTTP and HTTPS URLs without credentials are allowed.") from None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not allow_local and not _public_ip(host):
            raise ValueError("Web fetch only permits public internet addresses.")
    resolver = aiohttp.resolver.DefaultResolver() if allow_local else PublicResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
    try:
        async with aiohttp.ClientSession(connector=connector, trust_env=False) as safe_session:
            async with safe_session.get(url, timeout=DEFAULT_TIMEOUT, allow_redirects=False) as resp:
                if resp.status != 200:
                    raise RuntimeError("HTTP {} from {}; redirects are not followed.".format(resp.status, url))
                content_type = resp.headers.get("Content-Type", "").lower()
                data = await resp.content.read(MAX_BYTES + 1)
    finally:
        await resolver.close()
    truncated_bytes = len(data) > MAX_BYTES
    text = data[:MAX_BYTES].decode("utf-8", errors="replace")
    if "html" in content_type or text.lstrip()[:1] == "<":
        text = html_to_text(text)
    if truncated_bytes or len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"
    return text
