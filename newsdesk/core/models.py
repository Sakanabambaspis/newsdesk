"""Domain model: Source, Watchlist, Item, Job, LogEntry, Workflow.

The Item row is the persistence form of the canonical record defined in
docs/DESIGN.md; ``Item.to_canonical()`` emits exactly that JSON shape.
The workflow tables (Workflow, WorkflowVersion) are the catalog's
storage: they live here so ``Database``'s ``create_all`` picks them up
with every other table, while their repo (WorkflowCatalog) stays in
``newsdesk.workflow.catalog``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TypedDict

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None) -> str | None:
    """ISO string with UTC attached — SQLite returns naive datetimes and
    all stored times are UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class CanonicalItem(TypedDict):
    """The canonical record (docs/DESIGN.md 3.2) — the system's central
    contract. ``normalize_capture`` produces it, ``ItemRepo.upsert`` consumes
    it, and ``Item.to_canonical`` emits the same shape back out.

    Section keys are fixed; the values inside each section are deliberately
    plain dicts (the sections are storage-shaped, not object-shaped)."""

    id: str
    source: dict[str, Any]      # publisher, url, kind, author
    timestamps: dict[str, Any]  # published_at, retrieved_at (ISO strings)
    content: dict[str, Any]     # title, text, media, transcript
    analysis: dict[str, Any]    # topics, entities, relevance, claims (+ enrichments)
    provenance: dict[str, Any]  # content_hash, extraction_method, url_canonical, snapshot_path, simhash, revision, duplicate_of


def _empty_analysis() -> dict[str, Any]:
    return {"topics": [], "entities": [], "relevance": None, "claims": []}


# Every source kind newsdesk knows. Fetchers exist for the first group
# (registered in newsdesk.ingest.base); the rest are staged kinds, accepted
# at add time and rejected at collect time until their fetcher ships.
SOURCE_KINDS: tuple[str, ...] = (
    "rss", "arxiv", "youtube", "video", "email", "twitter",
    "html", "sitemap", "api", "forum", "newsletter", "manual",
)


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: int | None = Field(default=None, primary_key=True)
    url: str = Field(index=True)  # feed/channel URL exactly as registered
    kind: str = Field(default="rss")  # one of SOURCE_KINDS
    title: str | None = None  # human label, usually the feed title
    publisher: str | None = None
    enabled: bool = Field(default=True)
    fetch_interval_minutes: int = Field(default=30)
    etag: str | None = None  # conditional-GET state, or fetcher state token (e.g. imap-uid:<n>)
    last_modified: str | None = None
    last_fetched_at: datetime | None = None
    last_status: str | None = None  # ok | not_modified | error:<msg> | skipped:<reason>
    notes: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Watchlist(SQLModel, table=True):
    __tablename__ = "watchlists"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class WatchlistTerm(SQLModel, table=True):
    __tablename__ = "watchlist_terms"

    id: int | None = Field(default=None, primary_key=True)
    watchlist_id: int = Field(foreign_key="watchlists.id", index=True)
    term: str = Field(index=True)
    kind: str = Field(default="include")  # include | exclude | entity | topic
    weight: float = Field(default=1.0)


class WatchlistSource(SQLModel, table=True):
    __tablename__ = "watchlist_sources"

    watchlist_id: int = Field(foreign_key="watchlists.id", primary_key=True)
    source_id: int = Field(foreign_key="sources.id", primary_key=True)


class Item(SQLModel, table=True):
    __tablename__ = "items"

    id: str = Field(primary_key=True)  # item_<hash of canonical url>
    source_id: int = Field(foreign_key="sources.id", index=True)
    publisher: str | None = None
    url: str = Field(index=True)  # original URL as published in the feed
    url_canonical: str = Field(index=True, unique=True)
    kind: str = Field(default="article")  # article | video | audio | post | document
    author: str | None = None
    published_at: datetime | None = Field(default=None, index=True)
    retrieved_at: datetime = Field(default_factory=utcnow, index=True)
    title: str = ""
    text: str = ""
    media: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    transcript: str | None = None
    analysis: dict[str, Any] = Field(default_factory=_empty_analysis, sa_column=Column(JSON))
    content_hash: str = Field(index=True)
    simhash: str | None = None
    extraction_method: str = Field(default="rss")
    snapshot_path: str | None = None
    duplicate_of: str | None = Field(default=None, foreign_key="items.id", index=True)
    revision: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def to_canonical(self) -> CanonicalItem:
        """Emit the canonical record contract from docs/DESIGN.md section 3.2."""
        return {
            "id": self.id,
            "source": {
                "publisher": self.publisher,
                "url": self.url,
                "kind": self.kind,
                "author": self.author,
            },
            "timestamps": {
                "published_at": iso_utc(self.published_at),
                "retrieved_at": iso_utc(self.retrieved_at),
            },
            "content": {
                "title": self.title,
                "text": self.text,
                "media": self.media or [],
                "transcript": self.transcript,
            },
            "analysis": self.analysis or _empty_analysis(),
            "provenance": {
                "content_hash": self.content_hash,
                "extraction_method": self.extraction_method,
                "url_canonical": self.url_canonical,
                "snapshot_path": self.snapshot_path,
                "simhash": self.simhash,
                "revision": self.revision,
                "duplicate_of": self.duplicate_of,
            },
        }


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    status: str = Field(default="running")  # running | done | partial | error
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    stats: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error: str | None = None


class LogEntry(SQLModel, table=True):
    """Append-only activity log. Nothing in the system ever updates these rows."""

    __tablename__ = "log_entries"

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    actor: str = Field(default="system")  # one of ACTORS
    action: str = Field(index=True)
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "ts": iso_utc(self.ts), "actor": self.actor,
                "action": self.action, "detail": self.detail}


# Who performed a logged/cataloged mutation. The canonical vocabulary for
# every required-actor parameter (the workflow catalog refuses anything
# else — a silently-defaulted actor is the mis-attribution that matters).
ACTORS: tuple[str, ...] = ("system", "user", "agent")


class Workflow(SQLModel, table=True):
    """A workflow name in the catalog (wayfinder ticket 04).

    Rows are created implicitly by the name's first version and are never
    deleted; retirement is this row's flag (retire-never-delete), so the
    versions below stay strictly append-only.
    """

    __tablename__ = "workflows"

    name: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=utcnow)
    retired_at: datetime | None = None


class WorkflowVersion(SQLModel, table=True):
    """One immutable version of a workflow: INSERT-only by design — the
    repos expose no update or delete for this table, a mistaken version
    is superseded by the next one, never corrected."""

    __tablename__ = "workflow_versions"

    name: str = Field(primary_key=True, foreign_key="workflows.name")
    version: int = Field(primary_key=True)
    document: dict[str, Any] = Field(sa_column=Column(JSON))
    created_by: str  # one of ACTORS; required — no default to hide behind
    created_at: datetime = Field(default_factory=utcnow)
