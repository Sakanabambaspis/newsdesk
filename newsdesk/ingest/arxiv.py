"""arXiv fetcher: uses the official arXiv Atom API, not the blocked web paths.

export.arxiv.org serves ``User-agent: * Disallow: /`` (arXiv's blanket
anti-crawler posture), so the RSS paths are off-limits to a robots-compliant
agent. The *published programmatic interface* is the arXiv API, whose terms
explicitly permit automated queries under an etiquette this fetcher follows:

- identified User-Agent (settings.user_agent already self-describes),
- >= 3 seconds between requests (HttpHelper enforces min interval per host),
- small page sizes, one request per collection run per category.

Source URL shape: ``arxiv://cs.LG`` (category listing, newest first).
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

import feedparser

from ..config import Settings
from ..core.ids import sha256_hex
from ..core.models import Source
from .base import FetchError, Fetcher, RawCapture, RawEntry, register
from .fetcher import HttpHelper
from .snapshots import save_snapshot
from .textutil import strip_html

API_URL = "https://export.arxiv.org/api/query"


def _parse_source_category(source_url: str) -> str:
    scheme, _, rest = source_url.partition("://")
    if scheme != "arxiv" or not rest.strip("/"):
        raise FetchError(
            f"invalid arxiv source URL '{source_url}' (expected arxiv://<category>)")
    return rest.strip("/")


@register
class ArxivFetcher(Fetcher):
    kind = "arxiv"

    def fetch(self, source: Source, settings: Settings,
              http: HttpHelper | None = None) -> RawCapture:
        fetched_at = datetime.now(timezone.utc)
        if http is None:
            raise FetchError("ArxivFetcher requires an HttpHelper")

        category = _parse_source_category(source.url)
        # The API is a published interface; robots.txt governs web crawling,
        # so the allowed() check is deliberately skipped here. See module doc.
        query = urlencode({
            "search_query": f"cat:{category}",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": 0,
            "max_results": min(settings.max_items_per_feed, 100),
        })
        try:
            response = http.get(f"{API_URL}?{query}")
        except Exception as exc:
            raise FetchError(f"arXiv API fetch failed for {category}: {exc}") from exc
        if response.status_code >= 400:
            raise FetchError(f"arXiv API HTTP {response.status_code} for {category}")

        payload = response.content
        payload_hash = sha256_hex(payload)
        parsed = feedparser.parse(payload)
        if parsed.bozo and not parsed.entries:
            raise FetchError(f"unparseable arXiv response for {category}: "
                             f"{parsed.get('bozo_exception')}")

        entries: list[RawEntry] = []
        for entry in parsed.entries:
            link = entry.get("link", "")
            if not link:
                continue
            authors = entry.get("authors") or []
            entries.append(RawEntry(
                url=link,
                title=strip_html(entry.get("title", "") or "").replace("\n", " "),
                text=strip_html(entry.get("summary", "") or ""),
                published_at=_entry_datetime(entry),
                author=", ".join(a.get("name", "") for a in authors[:4])
                or entry.get("author"),
            ))

        return RawCapture(
            source_url=source.url,
            fetched_at=fetched_at,
            content_hash=payload_hash,
            extraction_method=self.kind,
            snapshot_path=save_snapshot(settings, source_id=source.id,
                                        payload=payload, ext="atom",
                                        when=fetched_at),
            entries=entries[: settings.max_items_per_feed],
            status="ok",
            content_kind="document",
            meta={
                "feed_title": f"arXiv {category}",
                "etag": response.headers.get("etag"),
                "category": category,
            },
        )


def _entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            return datetime(*tm[:6], tzinfo=timezone.utc)
    return None
