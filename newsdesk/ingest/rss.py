"""RSS/Atom fetcher — the preferred low-cost ingestion path."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import feedparser

from ..config import Settings
from ..core.ids import sha256_hex
from ..core.models import Source
from .base import FetchError, Fetcher, RawCapture, RawEntry, register
from .fetcher import HttpHelper, read_local, resolve_local
from .snapshots import save_snapshot
from .textutil import strip_html


def _entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        tm = entry.get(key)
        if tm:
            return datetime(*tm[:6], tzinfo=timezone.utc)
    return None


def _entry_text(entry) -> str:
    # Prefer full content when the publisher syndicates it; fall back to summary.
    for content in entry.get("content", []) or []:
        value = content.get("value", "")
        if value:
            return strip_html(value)
    return strip_html(entry.get("summary", "") or "")


def _entry_media(entry) -> list[dict]:
    media: list[dict] = []
    for enclosure in entry.get("enclosures", []) or []:
        if enclosure.get("href"):
            media.append({"url": enclosure["href"], "type": enclosure.get("type"),
                          "length": enclosure.get("length")})
    for mc in entry.get("media_content", []) or []:
        if mc.get("url"):
            media.append({"url": mc["url"], "type": mc.get("type"),
                          "medium": mc.get("medium")})
    return media


@register
class RSSFetcher(Fetcher):
    kind = "rss"

    def fetch(self, source: Source, settings: Settings, http: HttpHelper | None = None) -> RawCapture:
        fetched_at = datetime.now(timezone.utc)
        local = resolve_local(source.url)
        if local is not None:
            if not local.exists():
                raise FetchError(f"local file not found: {local}")
            payload = read_local(local)
            etag = last_modified = None
            response_headers: dict[str, str] = {}
        else:
            if http is None:
                raise FetchError("RSSFetcher requires an HttpHelper for remote URLs")
            allowed, reason = http.allowed(source.url)
            if not allowed:
                return RawCapture(
                    source_url=source.url, fetched_at=fetched_at,
                    content_hash="", extraction_method=self.kind,
                    status="skipped", meta={"skip_reason": reason},
                )
            try:
                response = http.get(source.url, etag=source.etag,
                                    last_modified=source.last_modified)
            except Exception as exc:
                raise FetchError(f"fetch failed for {source.url}: {exc}") from exc
            if response.status_code == 304:
                return RawCapture(
                    source_url=source.url, fetched_at=fetched_at,
                    content_hash="", extraction_method=self.kind,
                    status="not_modified",
                )
            if response.status_code >= 400:
                raise FetchError(f"HTTP {response.status_code} for {source.url}")
            payload = response.content
            response_headers = dict(response.headers)
            etag = response_headers.get("etag")
            last_modified = response_headers.get("last-modified")

        payload_hash = sha256_hex(payload)
        snapshot_path = save_snapshot(settings, source_id=source.id,
                                      payload=payload, ext="xml",
                                      when=fetched_at)

        parsed = feedparser.parse(payload)
        bozo = getattr(parsed, "bozo", 0)
        if bozo and not parsed.entries:
            raise FetchError(f"unparseable feed {source.url}: {parsed.get('bozo_exception')}")

        entries: list[RawEntry] = []
        # Site-relative links resolve against the publisher's web base. A local
        # file import has no host of its own, so only the feed's own <link>
        # can anchor them (otherwise the entry is skipped — no anchorable
        # provenance).
        link_base = source.url if local is None else (parsed.feed.get("link") or "")
        for entry in parsed.entries[: settings.max_items_per_feed]:
            link = entry.get("link", "")
            if not link:
                continue  # nothing to anchor provenance to
            if not urlsplit(link).scheme:
                if not link_base:
                    continue
                link = urljoin(link_base, link)
            entries.append(RawEntry(
                url=link,
                title=strip_html(entry.get("title", "") or ""),
                text=_entry_text(entry),
                published_at=_entry_datetime(entry),
                author=entry.get("author"),
                media=_entry_media(entry),
            ))

        return RawCapture(
            source_url=source.url,
            fetched_at=fetched_at,
            content_hash=payload_hash,
            extraction_method=self.kind,
            snapshot_path=snapshot_path,
            entries=entries,
            status="ok",
            meta={
                "feed_title": parsed.feed.get("title"),
                "feed_link": parsed.feed.get("link"),
                "etag": etag,
                "last_modified": last_modified,
                "bozo": bool(bozo),
            },
        )
