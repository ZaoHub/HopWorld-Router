"""Read and combine plain-text or Base64 node-link subscriptions."""

import base64
import binascii
from urllib.parse import urlsplit

MAX_SUBSCRIPTION_BYTES = 2 * 1024 * 1024
NODE_SCHEMES = frozenset({"vless", "vmess", "trojan", "ss", "hysteria2", "hy2", "tuic", "anytls", "wireguard", "wg", "masque", "socks", "socks5", "http", "https"})


def _check_link(link: str) -> None:
    if not isinstance(link, str) or not link or link != link.strip() or any(c.isspace() for c in link):
        raise ValueError("Each node must be a single link")
    try:
        parts = urlsplit(link)
    except ValueError as exc:
        raise ValueError("Invalid node link") from exc
    if parts.scheme.lower() not in NODE_SCHEMES or not parts.netloc:
        raise ValueError("Unsupported or invalid node link")


def parse_subscription(content: bytes) -> tuple[list[str], int]:
    """Return links and the number of Clash nodes skipped during conversion."""
    if not isinstance(content, bytes) or len(content) > MAX_SUBSCRIPTION_BYTES:
        raise ValueError("Invalid or oversized subscription")
    try:
        text = content.decode("utf-8-sig").strip()
        if any(line.lstrip().startswith('proxies:') for line in text.splitlines()):
            from router.clash import parse_clash
            return parse_clash(text)
        if "://" not in text:
            encoded = "".join(text.split()).encode("ascii")
            encoded += b"=" * (-len(encoded) % 4)
            text = base64.b64decode(encoded, altchars=b"-_", validate=True).decode("utf-8-sig").strip()
    except (UnicodeError, binascii.Error, ValueError) as exc:
        raise ValueError("Subscription must be UTF-8 text or Base64") from exc
    links = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not links:
        raise ValueError("Subscription contains no nodes")
    for link in links:
        _check_link(link)
    return links, 0


def decode_subscription(content: bytes) -> list[str]:
    return parse_subscription(content)[0]


def combine_subscriptions(subscriptions: list[list[str]]) -> list[str]:
    """Merge exact duplicate links, preserving the first occurrence."""
    result = []
    seen = set()
    for links in subscriptions:
        for link in links:
            _check_link(link)
            if link not in seen:
                seen.add(link)
                result.append(link)
    return result


def encode_subscription(links: list[str]) -> str:
    """Encode individual node links without silently changing their contents."""
    if not links:
        raise ValueError("At least one node is required")
    for link in links:
        _check_link(link)
    return base64.b64encode("\n".join(links).encode("utf-8")).decode("ascii")
