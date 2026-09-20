"""Stations: one published podcast surface each (W4, wayfinder ticket 10).

A Station binds a scope (one watchlist and, through it, its sources), a
workflow ref, a feed identity and one publish path — exactly one episode
per (station, date). This module is the only layer that talks to the
``stations`` table, with the catalogs' conventions:

- Retire-never-delete: rows are never removed; retirement is the row's
  ``retired_at`` flag (monotonic — a retired station refuses runs, the
  published archive is never touched, and a seed can retire but never
  un-retire). Un-retire stays a deliberate logged action.
- Every mutation takes a *required* ``actor`` and lands in the log with
  a ``via`` origin (``station_created`` / ``station_updated`` /
  ``station_retired`` / ``station_unretired``). No credentials column, ever.
- ``name`` and ``path_segment`` are immutable — GUIDs and enclosure URLs
  are permanent by publish contract, so the repo exposes no rename and no
  path edit at all; the API surface is the enforcement.
- The feed identity columns are mutable metadata: the feed is regenerated
  whole on every publish, so metadata changes never touch GUIDs/URLs.
  NULLs fall back to ``morning.feed``'s module constants, so the seeded
  default station reproduces today's feed byte-for-byte.

The engine stays station-table-aware only here: at pre-flight it resolves
the row (loud on missing/retired), and :func:`station_scope` turns the
row's watchlist binding into the digest scope that select stages thread
into ``candidate_items`` — the strategies themselves stay stations-table-
blind. :func:`resolve_identity` and :func:`publish_targets` are the
publish-side view the publishers consume via the episode payload.
"""

from __future__ import annotations

import re
from typing import Any

from sqlmodel import Session, col, select

from ..core.models import (ACTORS, Station, iso_utc, utcnow)
from ..morning.feed import (FEED_AUTHOR, FEED_CATEGORY, FEED_DESCRIPTION,
                            FEED_TITLE)
from ..storage.repo import LogRepo, WatchlistRepo
from .catalog import CatalogError, DEFAULT_WORKFLOW

DEFAULT_STATION = "morning-briefing"
DEFAULT_WORKFLOW_REF = DEFAULT_WORKFLOW

# name is the one identity: CLI arg, GUID prefix, log field. path_segment
# rides the same shape (one URL path segment under the feed token).
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

# path segments that would collide with the legacy root's own files
# (<token>/audio/…, <token>/feed.xml, …) — refused at every write.
RESERVED_SEGMENTS = frozenset({"audio", "feed.xml", "episodes.json",
                               "artwork.png"})

FEED_IDENTITY_KEYS = ("title", "description", "author", "category",
                      "language", "owner_email")


def _require_actor(actor: str) -> None:
    if actor not in ACTORS:
        raise CatalogError(f"actor must be one of {', '.join(ACTORS)}, "
                           f"got {actor!r}")


def _row_dict(row: Station) -> dict[str, Any]:
    return {
        "name": row.name,
        "description": row.description,
        "workflow_ref": row.workflow_ref,
        "watchlist_id": row.watchlist_id,
        "path_segment": row.path_segment,
        "feed_title": row.feed_title,
        "feed_description": row.feed_description,
        "feed_author": row.feed_author,
        "feed_category": row.feed_category,
        "feed_language": row.feed_language,
        "feed_owner_email": row.feed_owner_email,
        "created_at": iso_utc(row.created_at),
        "retired_at": iso_utc(row.retired_at),
    }


class StationRepo:
    """Repo-layer access to ``stations`` (the LogRepo idiom: only this
    layer talks to the table, and only through operations the semantics
    allow)."""

    def __init__(self, session: Session):
        self.session = session

    # -- reads

    def get(self, name: str) -> dict[str, Any] | None:
        """One station as a plain dict, or ``None`` — loud resolution is
        :meth:`resolve`'s job."""
        row = self.session.get(Station, name)
        return None if row is None else _row_dict(row)

    def list(self) -> list[dict[str, Any]]:
        """Every station, ordered by name — retired ones included (their
        published archives stay live; a deploy must never drop them)."""
        return [_row_dict(row) for row in self.session.exec(
            select(Station).order_by(col(Station.name))).all()]

    def resolve(self, name: str) -> dict[str, Any]:
        """The run-time resolution path: loud on unknown and retired —
        a station run never silently falls back to another station or to
        station-less behavior."""
        row = self.session.get(Station, name)
        if row is None:
            known = ", ".join(s["name"] for s in self.list()) or "none defined"
            raise CatalogError(f"unknown station '{name}' ({known})")
        if row.retired_at is not None:
            raise CatalogError(
                f"station '{name}' is retired (since "
                f"{iso_utc(row.retired_at)}) — un-retire it or rebind "
                f"what references it")
        return _row_dict(row)

    # -- writes (every one: required actor, logged)

    def create(self, name: str, *, actor: str, via: str | None = None,
               description: str | None = None,
               workflow_ref: str = DEFAULT_WORKFLOW_REF,
               watchlist_id: int | None = None,
               path_segment: str | None = None,
               feed_title: str | None = None,
               feed_description: str | None = None,
               feed_author: str | None = None,
               feed_category: str | None = None,
               feed_language: str | None = None,
               feed_owner_email: str | None = None) -> dict[str, Any]:
        """Register one station; loud on duplicates, bad slugs, a taken
        or reserved path segment, or an unknown watchlist. The workflow
        ref is stored as given — float/pin semantics are resolved at run
        start (ticket 04), never checked against the catalog here."""
        _require_actor(actor)
        if not isinstance(name, str) or not SLUG_RE.fullmatch(name):
            raise CatalogError(
                f"station name must match {SLUG_RE.pattern}, got {name!r}")
        if self.session.get(Station, name) is not None:
            raise CatalogError(f"station '{name}' already exists")
        if workflow_ref is None or not str(workflow_ref).strip():
            raise CatalogError(f"station '{name}': workflow_ref must be a "
                               f"non-empty ref string")
        if watchlist_id is not None and \
                WatchlistRepo(self.session).get(watchlist_id) is None:
            raise CatalogError(f"station '{name}': watchlist "
                               f"{watchlist_id} does not exist")
        path_segment = self._validated_segment(name, path_segment)
        row = Station(name=name, description=description,
                      workflow_ref=str(workflow_ref).strip(),
                      watchlist_id=watchlist_id, path_segment=path_segment,
                      feed_title=feed_title, feed_description=feed_description,
                      feed_author=feed_author, feed_category=feed_category,
                      feed_language=feed_language,
                      feed_owner_email=feed_owner_email)
        self.session.add(row)
        self.session.commit()
        self._log("station_created", name, via, actor)
        return _row_dict(row)

    def update_identity(self, name: str, *, actor: str, via: str | None = None,
                        description: str | None = None,
                        workflow_ref: str | None = None,
                        watchlist_id: int | None = None,
                        feed_title: str | None = None,
                        feed_description: str | None = None,
                        feed_author: str | None = None,
                        feed_category: str | None = None,
                        feed_language: str | None = None,
                        feed_owner_email: str | None = None) -> dict[str, Any]:
        """Replace the mutable metadata of one station (full-document
        semantics — the seed and the agent tools carry whole documents).
        ``name`` and ``path_segment`` are not parameters: they are
        immutable, and nothing here can change them."""
        _require_actor(actor)
        row = self._require_name(name)
        if workflow_ref is None or not str(workflow_ref).strip():
            raise CatalogError(f"station '{name}': workflow_ref must be a "
                               f"non-empty ref string")
        if watchlist_id is not None and \
                WatchlistRepo(self.session).get(watchlist_id) is None:
            raise CatalogError(f"station '{name}': watchlist "
                               f"{watchlist_id} does not exist")
        row.description = description
        row.workflow_ref = str(workflow_ref).strip()
        row.watchlist_id = watchlist_id
        row.feed_title = feed_title
        row.feed_description = feed_description
        row.feed_author = feed_author
        row.feed_category = feed_category
        row.feed_language = feed_language
        row.feed_owner_email = feed_owner_email
        self.session.add(row)
        self.session.commit()
        self._log("station_updated", name, via, actor)
        return _row_dict(row)

    def retire(self, name: str, *, actor: str,
               via: str | None = None) -> None:
        """Flag the station retired: runs are refused from now on, the
        published archive stays untouched. Idempotent."""
        _require_actor(actor)
        row = self._require_name(name)
        if row.retired_at is not None:
            return
        row.retired_at = utcnow()
        self.session.add(row)
        self.session.commit()
        self._log("station_retired", name, via, actor)

    def unretire(self, name: str, *, actor: str,
                 via: str | None = None) -> None:
        """Clear the retirement flag (the row was never gone). Logged;
        idempotent like ``retire``."""
        _require_actor(actor)
        row = self._require_name(name)
        if row.retired_at is None:
            return
        row.retired_at = None
        self.session.add(row)
        self.session.commit()
        self._log("station_unretired", name, via, actor)

    # -- internals

    def _log(self, action: str, name: str, via: str | None,
             actor: str) -> None:
        detail: dict[str, Any] = {"station": name}
        if via:
            detail["via"] = via
        LogRepo(self.session).append(action, detail, actor=actor)

    def _require_name(self, name: str) -> Station:
        row = self.session.get(Station, name)
        if row is None:
            raise CatalogError(f"unknown station '{name}'")
        return row

    def _validated_segment(self, name: str,
                           path_segment: str | None) -> str | None:
        if path_segment is None:
            return None
        if not isinstance(path_segment, str) or \
                not SLUG_RE.fullmatch(path_segment):
            raise CatalogError(
                f"station '{name}': path_segment must match "
                f"{SLUG_RE.pattern}, got {path_segment!r}")
        if path_segment in RESERVED_SEGMENTS:
            raise CatalogError(
                f"station '{name}': path_segment '{path_segment}' is "
                f"reserved (the legacy root's own files live there)")
        taken = self.session.exec(
            select(Station).where(Station.path_segment == path_segment)
        ).first()
        if taken is not None:
            raise CatalogError(
                f"station '{name}': path_segment '{path_segment}' is "
                f"already taken by station '{taken.name}'")
        return path_segment


# -- the run-time and publish-side views ---------------------------------------

def station_scope(session: Session, watchlist_id: int | None) -> dict[str, Any]:
    """The digest scope a station's watchlist binding resolves to (F1,
    ticket 10): include/exclude terms plus the attached-source filter.

    ``watchlist_id=None`` is the global include-terms union — today's
    ``WatchlistRepo.include_terms()`` — with no excludes and all sources,
    so station-less behavior stays byte-identical. A bound watchlist
    scopes to exactly its terms; source scoping rides its
    ``watchlist_sources`` attachment (none attached → all sources)."""
    wrepo = WatchlistRepo(session)
    if watchlist_id is None:
        return {"include_terms": wrepo.include_terms(),
                "exclude_terms": [], "source_ids": None}
    watchlist = wrepo.get(watchlist_id)
    if watchlist is None:
        raise CatalogError(
            f"station watchlist {watchlist_id} does not exist — the "
            f"binding is stale (the scope must never fall back to global)")
    terms = wrepo.terms(watchlist_id)
    return {
        "include_terms": [(t.term, t.weight) for t in terms
                          if t.kind == "include"],
        "exclude_terms": [t.term for t in terms if t.kind == "exclude"],
        "source_ids": wrepo.source_ids(watchlist_id) or None,
    }


def resolve_identity(station: dict[str, Any]) -> dict[str, Any]:
    """The feed identity a station publishes under: each NULL field falls
    back to ``morning.feed``'s module constants, so the seeded default
    station reproduces today's feed byte-for-byte. ``owner_email`` stays
    ``None`` when unset — the publisher applies the env/fallback chain."""
    return {
        "title": station.get("feed_title") or FEED_TITLE,
        "description": station.get("feed_description") or FEED_DESCRIPTION,
        "author": station.get("feed_author") or FEED_AUTHOR,
        "category": station.get("feed_category") or FEED_CATEGORY,
        "language": station.get("feed_language") or "en",
        "owner_email": station.get("feed_owner_email"),
    }


def publish_target(station: dict[str, Any]) -> dict[str, Any]:
    """The publish-side view one station contributes to the episode
    payload: the station name (the GUID prefix), its path segment under
    the shared token, and its resolved feed identity."""
    return {"station": station["name"],
            "path_segment": station["path_segment"],
            "feed": resolve_identity(station)}
