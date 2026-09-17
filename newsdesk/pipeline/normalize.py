"""Normalization: RawCapture entries -> canonical item records.

The canonical record shape is the storage contract from docs/DESIGN.md
section 3.2 (id / source / timestamps / content / analysis / provenance).
"""

from __future__ import annotations

from ..core.ids import canonical_url, content_hash, item_id_for, simhash64
from ..core.models import CanonicalItem, Source
from ..ingest.base import RawCapture


def relevance_score(title: str, text: str,
                    include_terms: list[tuple[str, float]]) -> float | None:
    """Naive keyword relevance for v1: weighted fraction of include-terms present.

    This is a bootstrapping signal only — never presented as an objective
    credibility measure (see DESIGN.md section 7). None when no watchlist
    terms exist yet.
    """
    if not include_terms:
        return None
    haystack = f"{title}\n{text}".lower()
    total_weight = sum(w for _, w in include_terms)
    matched = sum(w for term, w in include_terms if term.lower() in haystack)
    return round(matched / total_weight, 4) if total_weight else None


def normalize_capture(capture: RawCapture, source: Source,
                      include_terms: list[tuple[str, float]] | None = None) \
        -> list[CanonicalItem]:
    publisher = source.publisher or capture.meta.get("feed_title") or source.title
    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for entry in capture.entries:
        url_canonical = canonical_url(entry.url)
        if url_canonical in seen_urls:  # same story twice inside one feed
            continue
        if not (entry.title or entry.text or entry.transcript):
            continue  # nothing to anchor a claim or a content hash to
        seen_urls.add(url_canonical)
        text = entry.text or ""
        items.append({
            "id": item_id_for(entry.url),
            "source": {
                "publisher": publisher,
                "url": entry.url,
                "kind": capture.content_kind,
                "author": entry.author,
            },
            "timestamps": {
                "published_at": entry.published_at.isoformat() if entry.published_at else None,
                "retrieved_at": capture.fetched_at.isoformat(),
            },
            "content": {
                "title": entry.title or "",
                "text": text,
                "media": entry.media,
                "transcript": entry.transcript,
            },
            "analysis": {
                "topics": [],
                "entities": [],
                "relevance": relevance_score(entry.title or "", text, include_terms or []),
                "claims": [],
            },
            "provenance": {
                "content_hash": content_hash(title=entry.title or "", text=text),
                "extraction_method": capture.extraction_method,
                "url_canonical": url_canonical,
                "snapshot_path": capture.snapshot_path,
                "simhash": simhash64(f"{entry.title or ''}\n{text}"),
                "revision": 0,
            },
        })
    return items
