"""The anonymous podcast feed artifact (wayfinder tickets 02/07/10).

One builder regenerates the whole feed from the episode manifest on every
publish: append-only episode list, permanent GUIDs and enclosure URLs,
anonymous generic metadata, and no digest item lists or show notes anywhere
in the XML — a scraped feed reveals nothing; the interest profile lives in
the audio only.
"""

from __future__ import annotations

import struct
import zlib
from datetime import datetime
from email.utils import format_datetime
from typing import Any
from xml.sax.saxutils import escape, quoteattr

FEED_TITLE = "Morning Briefing"
FEED_DESCRIPTION = "A short daily technology briefing."
FEED_AUTHOR = "Morning Briefing"  # pseudonymous on purpose (ticket 07)
FEED_CATEGORY = "News"
FEED_OWNER_EMAIL_FALLBACK = "morning-briefing@example.com"  # alias; set yours via NEWSDESK_FEED_OWNER_EMAIL
ARTWORK_SIZE = 1400  # Apple requires 1400-3000px square artwork


def episode_title(date: str) -> str:
    """Date-only episode titles (ticket 07)."""
    return date


def episode_guid(date: str) -> str:
    """Permanent GUID, never reused across dates (ticket 10)."""
    return f"morning-briefing-{date}"


def build_feed_xml(episodes: list[dict[str, Any]], *, feed_url: str,
                   owner_email: str, artwork_url: str) -> str:
    """Regenerate the complete RSS 2.0 + itunes feed.

    ``episodes`` are resolved feed rows: {date, enclosure_url, bytes,
    duration_seconds, published_at}. Listed newest first; every episode ever
    published stays listed (ticket 10).
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" '
        'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">',
        "<channel>",
        f"<title>{escape(FEED_TITLE)}</title>",
        f"<link>{escape(feed_url)}</link>",
        f"<description>{escape(FEED_DESCRIPTION)}</description>",
        "<language>en</language>",
        f"<itunes:author>{escape(FEED_AUTHOR)}</itunes:author>",
        "<itunes:explicit>false</itunes:explicit>",
        f"<itunes:image href={quoteattr(artwork_url)} />",
        "<itunes:category text=" + quoteattr(FEED_CATEGORY) + " />",
        "<itunes:owner>",
        f"<itunes:email>{escape(owner_email)}</itunes:email>",
        "</itunes:owner>",
    ]
    for ep in sorted(episodes, key=lambda e: e["date"], reverse=True):
        published_at = format_datetime(datetime.fromisoformat(ep["published_at"]))
        title = escape(episode_title(ep["date"]))
        guid = escape(episode_guid(ep["date"]))
        enclosure = (
            "<enclosure url=" + quoteattr(ep["enclosure_url"])
            + f' length="{int(ep["bytes"])}" type="audio/mpeg" />'
        )
        lines += [
            "<item>",
            f"<title>{title}</title>",
            f'<guid isPermaLink="false">{guid}</guid>',
            f"<pubDate>{escape(published_at)}</pubDate>",
            enclosure,
            f"<itunes:duration>{int(ep['duration_seconds'])}</itunes:duration>",
            "</item>",
        ]
    lines += ["</channel>", "</rss>"]
    return "\n".join(lines) + "\n"


def write_artwork(path) -> None:
    """Neutral solid-color square PNG at Apple's 1400px minimum.

    Generated in code (no binary asset in the repo); deterministic bytes.
    """
    size = ARTWORK_SIZE
    row = b"\x00" + b"\xf5\xf2\xec" * size  # PNG filter 0 + one RGB line
    idat = zlib.compress(row * size, 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", idat) + chunk(b"IEND", b""))
