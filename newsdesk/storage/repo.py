"""Repositories: the only layer that talks to tables.

ItemRepo.upsert implements tier-1 deduplication: same canonical URL with the
same content hash is a no-op; same URL with a changed hash is a revision;
a brand-new URL whose content hash matches an existing item is recorded as a
syndicated duplicate (kept, linked via duplicate_of, never silently dropped).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, col, or_, select

from ..core.models import (SOURCE_KINDS, CanonicalItem, Item, Job, LogEntry,
                           Source, Watchlist, WatchlistSource, WatchlistTerm)
from . import fts


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SourceRepo:
    def __init__(self, session: Session):
        self.session = session

    def add(self, url: str, kind: str = "rss", title: str | None = None,
            publisher: str | None = None, fetch_interval_minutes: int = 30) -> tuple[Source, bool]:
        """Register a source; idempotent on (url, kind).

        Unknown kinds are rejected here, at add time, per the DESIGN.md
        stage-1 contract ("invalid source rejected at add time") — never
        silently deferred to collection.
        """
        if kind not in SOURCE_KINDS:
            raise ValueError(
                f"unknown source kind '{kind}' (known: {', '.join(SOURCE_KINDS)})")
        existing = self.session.exec(
            select(Source).where(Source.url == url, Source.kind == kind)
        ).first()
        if existing:
            return existing, False
        source = Source(url=url, kind=kind, title=title, publisher=publisher,
                        fetch_interval_minutes=fetch_interval_minutes)
        self.session.add(source)
        self.session.commit()
        self.session.refresh(source)
        return source, True

    def get(self, source_id: int) -> Source | None:
        return self.session.get(Source, source_id)

    def list(self, enabled_only: bool = False) -> list[Source]:
        stmt = select(Source).order_by(col(Source.id))
        if enabled_only:
            stmt = stmt.where(Source.enabled == True)  # noqa: E712
        return list(self.session.exec(stmt))

    def set_enabled(self, source_id: int, enabled: bool) -> Source | None:
        source = self.get(source_id)
        if source:
            source.enabled = enabled
            self.session.add(source)
            self.session.commit()
            self.session.refresh(source)
        return source

    def record_fetch(self, source: Source, *, status: str, etag: str | None = None,
                     last_modified: str | None = None) -> None:
        source.last_status = status
        source.last_fetched_at = _now()
        if etag is not None:
            source.etag = etag
        if last_modified is not None:
            source.last_modified = last_modified
        self.session.add(source)
        self.session.commit()


class ItemRepo:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, item: CanonicalItem, source_id: int) -> tuple[str, Item]:
        """Store a normalized canonical item. Returns (outcome, row).

        outcome: created | updated (revision) | unchanged | duplicate.
        """
        src = item["source"]
        prov = item["provenance"]
        content = item["content"]
        published = _parse_dt(item["timestamps"].get("published_at"))
        retrieved = _parse_dt(item["timestamps"].get("retrieved_at")) or _now()

        existing = self.session.exec(
            select(Item).where(Item.url_canonical == prov["url_canonical"])
        ).first()
        if existing:
            if existing.content_hash == prov["content_hash"]:
                return "unchanged", existing
            existing.title = content["title"]
            existing.text = content["text"]
            existing.media = content["media"] or []
            existing.transcript = content["transcript"]
            existing.analysis = item["analysis"]
            existing.content_hash = prov["content_hash"]
            existing.simhash = prov.get("simhash")
            existing.snapshot_path = prov.get("snapshot_path")
            existing.author = src.get("author")
            existing.published_at = published or existing.published_at
            existing.retrieved_at = retrieved
            existing.revision += 1
            existing.updated_at = _now()
            self.session.add(existing)
            self.session.commit()
            self.session.refresh(existing)
            return "updated", existing

        # Same content elsewhere = syndication, not a new event. Keep the row
        # (provenance) but link it to the first non-duplicate twin.
        twin = self.session.exec(
            select(Item).where(Item.content_hash == prov["content_hash"],
                               Item.duplicate_of == None)  # noqa: E712
        ).first()
        if twin is None:
            twin = self.session.exec(
                select(Item).where(Item.content_hash == prov["content_hash"])
            ).first()

        row = Item(
            id=item["id"],
            source_id=source_id,
            publisher=src.get("publisher"),
            url=src["url"],
            url_canonical=prov["url_canonical"],
            kind=src.get("kind", "article"),
            author=src.get("author"),
            published_at=published,
            retrieved_at=retrieved,
            title=content["title"],
            text=content["text"],
            media=content["media"] or [],
            transcript=content["transcript"],
            analysis=item["analysis"],
            content_hash=prov["content_hash"],
            simhash=prov.get("simhash"),
            extraction_method=prov.get("extraction_method", "rss"),
            snapshot_path=prov.get("snapshot_path"),
            duplicate_of=twin.id if twin else None,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return ("duplicate" if twin else "created"), row

    def get(self, item_id: str) -> Item | None:
        return self.session.get(Item, item_id)

    def patch_analysis(self, item_id: str, patch: dict[str, Any]) -> Item | None:
        """Merge keys into an item's analysis JSON (summaries, visual notes).

        Enrichment only — never touches content or provenance, so revisions
        from re-collection are unaffected.
        """
        row = self.get(item_id)
        if not row:
            return None
        analysis = dict(row.analysis or {})
        analysis.update(patch)
        row.analysis = analysis
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def set_transcript(self, item_id: str, transcript: str) -> Item | None:
        """Backfill a transcript (audio transcription after collection).

        How the transcript was obtained is recorded in analysis by the caller
        (e.g. analysis["transcript_source"]); provenance.extraction_method
        keeps describing how the item itself arrived.
        """
        row = self.get(item_id)
        if not row:
            return None
        row.transcript = transcript
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def recent(self, limit: int = 20) -> list[Item]:
        stmt = (
            select(Item)
            .order_by(col(Item.published_at).desc().nullslast(), col(Item.retrieved_at).desc())
            .limit(limit)
        )
        return list(self.session.exec(stmt))

    def search(self, query: str, limit: int = 20) -> list[Item]:
        ids = fts.search_ids(self.session, query, limit=limit)
        if ids is None:  # FTS unavailable -> LIKE fallback
            return self._like_search(query, limit)
        if not ids and fts.has_unsegmented_script(query):
            # unicode61 cannot segment CJK etc.: FTS is honestly empty while
            # the text really does contain the query, so match substrings.
            return self._like_search(query, limit)
        if not ids:
            return []
        rows = self.session.exec(select(Item).where(col(Item.id).in_(ids))).all()
        by_id = {r.id: r for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def _like_search(self, query: str, limit: int) -> list[Item]:
        like = f"%{query.strip()}%"
        stmt = (
            select(Item)
            .where(or_(col(Item.title).contains(like), col(Item.text).contains(like)))
            .order_by(col(Item.published_at).desc().nullslast())
            .limit(limit)
        )
        return list(self.session.exec(stmt))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class WatchlistRepo:
    def __init__(self, session: Session):
        self.session = session

    def create(self, name: str, description: str | None = None) -> Watchlist:
        wl = Watchlist(name=name, description=description)
        self.session.add(wl)
        self.session.commit()
        self.session.refresh(wl)
        return wl

    def get(self, watchlist_id: int) -> Watchlist | None:
        return self.session.get(Watchlist, watchlist_id)

    def list(self) -> list[Watchlist]:
        return list(self.session.exec(select(Watchlist).order_by(col(Watchlist.id))))

    def add_term(self, watchlist_id: int, term: str, kind: str = "include",
                 weight: float = 1.0) -> WatchlistTerm | None:
        if not self.get(watchlist_id):
            return None
        wt = WatchlistTerm(watchlist_id=watchlist_id, term=term.lower().strip(),
                           kind=kind, weight=weight)
        self.session.add(wt)
        self.session.commit()
        self.session.refresh(wt)
        return wt

    def attach_source(self, watchlist_id: int, source_id: int) -> bool:
        if not self.get(watchlist_id):
            return False
        existing = self.session.exec(
            select(WatchlistSource).where(WatchlistSource.watchlist_id == watchlist_id,
                                          WatchlistSource.source_id == source_id)
        ).first()
        if existing:
            return True
        self.session.add(WatchlistSource(watchlist_id=watchlist_id, source_id=source_id))
        self.session.commit()
        return True

    def include_terms(self) -> list[tuple[str, float]]:
        """All include-terms across watchlists: [(term, weight), ...].

        v1 relevance scoring is global; per-watchlist scoping arrives with
        digest generation (M4).
        """
        rows = self.session.exec(
            select(WatchlistTerm).where(WatchlistTerm.kind == "include")
        ).all()
        return [(r.term, r.weight) for r in rows]


class JobRepo:
    def __init__(self, session: Session):
        self.session = session

    def start(self) -> Job:
        job = Job(status="running")
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    def finish(self, job: Job, stats: dict[str, Any], error: str | None = None) -> Job:
        job.stats = stats
        job.finished_at = _now()
        job.error = error
        error_sources = stats.get("sources", {})
        any_error = any(s.get("error") for s in error_sources.values()) if error_sources else False
        job.status = "error" if error else ("partial" if any_error else "done")
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job


class LogRepo:
    """Append-only. No update or delete methods exist by design."""

    def __init__(self, session: Session):
        self.session = session

    def append(self, action: str, detail: dict[str, Any] | None = None,
               actor: str = "system") -> LogEntry:
        entry = LogEntry(actor=actor, action=action, detail=detail or {})
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def recent(self, limit: int = 50) -> list[LogEntry]:
        stmt = select(LogEntry).order_by(col(LogEntry.id).desc()).limit(limit)
        return list(self.session.exec(stmt))
