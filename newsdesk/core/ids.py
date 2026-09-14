"""Identity helpers: canonical URLs, content hashing, deterministic item IDs, simhash."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that only track the click, not the content.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src",
}


def canonical_url(url: str) -> str:
    """Normalize a URL so the same story always maps to the same identity.

    Lowercases scheme/host, drops default ports and tracking params, sorts the
    remaining query params, strips fragments and trailing slashes.
    """
    url = url.strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return url  # local path or malformed; return as-is
    scheme = parts.scheme.lower() or "http"
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    )
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(*, title: str, text: str) -> str:
    """Hash of what the publisher said: title + body, nothing else.

    Deliberately excludes the URL and normalizes whitespace, so a wire story
    republished verbatim (possibly re-wrapped) by two outlets produces the
    same hash — that is how syndication is detected. A revision that changes
    the wording also changes this hash, driving the item-revision path.
    """
    def norm(s: str) -> str:
        return " ".join((s or "").split())

    return sha256_hex("\n".join([norm(title), norm(text)]))


def item_id_for(url: str) -> str:
    """Deterministic item ID derived from the canonical URL.

    Re-fetching the same story maps to the same ID, which makes collection
    idempotent without any coordination.
    """
    return "item_" + sha256_hex(canonical_url(url))[:20]


_TOKEN_RE = re.compile(r"[a-z0-9']+")


def simhash64(text: str) -> str:
    """64-bit token-level simhash as 16 hex chars, for near-duplicate detection.

    Not yet used for clustering (M2); stored now so historical items can be
    clustered retroactively without refetching.
    """
    tokens = _TOKEN_RE.findall((text or "").lower())
    if not tokens:
        return "0" * 16
    bits = [0] * 64
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        h = int.from_bytes(digest[:8], "big")
        for i in range(64):
            bits[i] += 1 if h & (1 << i) else -1
    value = 0
    for i in range(64):
        if bits[i] > 0:
            value |= 1 << i
    return f"{value:016x}"


def hamming_distance(hex_a: str, hex_b: str) -> int:
    a, b = int(hex_a, 16), int(hex_b, 16)
    return bin(a ^ b).count("1")
