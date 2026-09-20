"""Seed files: sources, watchlists, workflows, and stations as committed JSON.

The Actions runner checks out only the git repo — the collection state
(sources, watchlist terms), the workflow catalog and the stations live in
NEWSDESK_HOME's database and never ride along. A seed file is the
versioned bridge: ``newsdesk seed export`` locally, commit, ``newsdesk
seed import`` at the top of every fresh database (CI, a new machine).

Import is idempotent on the same identity keys the database itself
uses: sources on (url, kind), watchlists on name, terms on
(watchlist, term, kind), workflow versions on (name, version),
stations on name.
Re-importing yesterday's seed never duplicates anything; fields you
changed locally re-export and win on the next commit.

Format 2 (workflow tickets 04/06, stations ticket 12) carries the catalog
sections: ``workflows`` carries full descriptor documents — they
self-describe ``name@version``, so no identity indirection is needed
within the section — and export writes the full history so a fresh DB can
serve stations pinned to old versions. Import of a version that already
exists is a no-op on parsed-equal documents and a loud ``SeedError`` on
different ones (history was violated somewhere; never overwrite).
Retirement applies monotonically: ``retired_workflows`` names retire on
import, and a stale seed can never un-retire — un-retire stays a
deliberate local action.

The ``stations`` section (ticket 10/12) is name-keyed documents with the
identity indirection (``workflow`` ref string, ``watchlist`` name,
``path_segment``, sparse ``feed`` identity object, ``retired`` flag).
Mutable metadata updates on import (a re-export wins, like sources);
a conflicting ``path_segment`` is a loud ``SeedError`` — path segments
are immutable (enclosure URLs are permanent). ``retired: true`` retires;
the flag's absence or falseness never un-retires.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from .storage.repo import SourceRepo, WatchlistRepo
from .workflow.catalog import CatalogError, WorkflowCatalog
from .workflow.schema import DescriptorError, require_valid
from .workflow.stations import (FEED_IDENTITY_KEYS, StationRepo)

FORMAT = 2


class SeedError(Exception):
    """Seed file is malformed or made-up; message is safe to log."""


def export_seed(session: Session) -> dict[str, Any]:
    """Snapshot every source, watchlist, workflow version, and station
    into a JSON-ready dict."""
    srepo, wrepo = SourceRepo(session), WatchlistRepo(session)
    source_key = {s.id: [s.url, s.kind] for s in srepo.list()}
    watchlist_name = {w.id: w.name for w in wrepo.list()}
    catalog = WorkflowCatalog(session)
    workflows: list[dict[str, Any]] = []
    retired: list[str] = []
    for entry in catalog.list():  # ordered by name
        workflows.extend(v["document"]
                         for v in catalog.versions(entry["name"]))
        if entry["retired_at"] is not None:
            retired.append(entry["name"])
    stations: list[dict[str, Any]] = []
    for s in StationRepo(session).list():  # ordered by name
        feed = {"title": s["feed_title"], "description": s["feed_description"],
                "author": s["feed_author"], "category": s["feed_category"],
                "language": s["feed_language"],
                "owner_email": s["feed_owner_email"]}
        doc: dict[str, Any] = {
            "name": s["name"],
            "description": s["description"],
            "workflow": s["workflow_ref"],
            "watchlist": watchlist_name.get(s["watchlist_id"]),
            "path_segment": s["path_segment"],
            "feed": {k: v for k, v in feed.items() if v is not None},
        }
        if s["retired_at"] is not None:
            doc["retired"] = True
        stations.append(doc)
    return {
        "format": FORMAT,
        "sources": [
            {"url": s.url, "kind": s.kind, "title": s.title,
             "publisher": s.publisher, "enabled": s.enabled,
             "fetch_interval_minutes": s.fetch_interval_minutes,
             "notes": s.notes}
            for s in srepo.list()
        ],
        "watchlists": [
            {
                "name": w.name,
                "description": w.description,
                "terms": [
                    {"term": t.term, "kind": t.kind, "weight": t.weight}
                    for t in wrepo.terms(w.id)
                ],
                "source_keys": [source_key[sid] for sid in wrepo.source_ids(w.id)
                                if sid in source_key],
            }
            for w in wrepo.list()
        ],
        "workflows": workflows,
        "retired_workflows": retired,
        "stations": stations,
    }


def import_seed(session: Session, data: dict[str, Any], *,
                actor: str) -> dict[str, int]:
    """Seed an (often empty) database from an exported dict, idempotently.

    ``actor`` is required (no default — a silently-defaulted import is
    the mis-attribution that matters); the CLI passes ``user``, and the
    workflow versions record it alongside ``via: seed`` in the log.

    Returns the added/present counts so the caller can report what
    actually happened. Never deletes: sources, terms, and versions that
    exist in the database but not in the file are left alone.
    """
    if not isinstance(data, dict):
        raise SeedError("seed file must be a JSON object")
    if data.get("format") != FORMAT:
        raise SeedError(f"unsupported seed format {data.get('format')!r} "
                        f"(expected {FORMAT}) — re-export with this version")
    srepo, wrepo = SourceRepo(session), WatchlistRepo(session)
    catalog = WorkflowCatalog(session)
    stations = StationRepo(session)
    stats = {"sources_added": 0, "sources_present": 0,
             "watchlists_created": 0, "terms_added": 0, "terms_present": 0,
             "links_added": 0, "links_present": 0,
             "workflows_added": 0, "workflows_present": 0,
             "stations_added": 0, "stations_updated": 0,
             "stations_present": 0}

    source_id_by_key: dict[tuple[str, str], int] = {}
    for i, entry in enumerate(_need_list(data.get("sources"), "sources")):
        _need(entry.get("url"), f"sources[{i}].url")
        try:
            source, created = srepo.add(entry["url"],
                                        kind=entry.get("kind") or "rss",
                                        title=entry.get("title"),
                                        publisher=entry.get("publisher"),
                                        fetch_interval_minutes=int(
                                            entry.get("fetch_interval_minutes") or 30))
        except ValueError as exc:
            raise SeedError(f"sources[{i}]: {exc}") from exc
        if entry.get("notes") and not source.notes:
            source.notes = entry["notes"]
            session.add(source)
            session.commit()
            session.refresh(source)
        if source.enabled != bool(entry.get("enabled", True)):
            srepo.set_enabled(source.id, bool(entry.get("enabled", True)))
        stats["sources_added" if created else "sources_present"] += 1
        source_id_by_key[(source.url, source.kind)] = source.id

    for i, entry in enumerate(_need_list(data.get("watchlists"), "watchlists")):
        _need(entry.get("name"), f"watchlists[{i}].name")
        watchlist = next((w for w in wrepo.list() if w.name == entry["name"]), None)
        if watchlist is None:
            watchlist = wrepo.create(entry["name"], entry.get("description"))
            stats["watchlists_created"] += 1
        existing_terms = {(t.term, t.kind) for t in wrepo.terms(watchlist.id)}
        for j, term in enumerate(_need_list(entry.get("terms"), f"terms #{i}")):
            _need(term.get("term"), f"watchlists[{i}].terms[{j}].term")
            key = (term["term"].lower().strip(), term.get("kind") or "include")
            if key in existing_terms:
                stats["terms_present"] += 1
                continue
            wrepo.add_term(watchlist.id, term["term"], kind=term.get("kind") or "include",
                           weight=float(term.get("weight") or 1.0))
            existing_terms.add(key)
            stats["terms_added"] += 1
        linked = set(wrepo.source_ids(watchlist.id))
        for j, key in enumerate(_need_list(entry.get("source_keys"),
                                           f"source_keys #{i}")):
            source_id = source_id_by_key.get((key[0], key[1])) if isinstance(key, list) \
                and len(key) == 2 else None
            if source_id is None:
                continue
            if source_id in linked:
                stats["links_present"] += 1
                continue
            wrepo.attach_source(watchlist.id, source_id)
            linked.add(source_id)
            stats["links_added"] += 1

    # -- workflows: full documents, imported in (name, version) order so
    #    the catalog's dense max+1 rule is satisfied by any well-formed
    #    file regardless of the order it lists entries in
    entries: list[tuple[int, dict[str, Any]]] = []
    for i, doc in enumerate(_need_list(data.get("workflows"), "workflows")):
        if not isinstance(doc, dict):
            raise SeedError(f"workflows[{i}] must be an object (a full "
                            f"descriptor document)")
        try:
            require_valid(doc)
        except DescriptorError as exc:
            raise SeedError(f"workflows[{i}]: {exc}") from exc
        entries.append((i, doc))
    entries.sort(key=lambda pair: (pair[1]["name"], pair[1]["version"]))
    for i, doc in entries:
        name, version = doc["name"], doc["version"]
        existing = catalog.get(name, version)
        if existing is not None:
            if existing != doc:
                raise SeedError(
                    f"workflows[{i}]: '{name}@{version}' already exists "
                    f"with a different document — history was violated; "
                    f"fix the seed or the database, never overwrite")
            stats["workflows_present"] += 1
            continue
        try:
            catalog.create_version(doc, actor=actor, via="seed")
        except CatalogError as exc:
            raise SeedError(f"workflows[{i}]: {exc}") from exc
        stats["workflows_added"] += 1

    # retirement applies monotonically: the file can retire, never un-retire
    for i, name in enumerate(_need_list(data.get("retired_workflows"),
                                        "retired_workflows")):
        if not isinstance(name, str):
            raise SeedError(f"retired_workflows[{i}] must be a workflow "
                            f"name string")
        try:
            catalog.retire(name, actor=actor, via="seed")
        except CatalogError as exc:
            raise SeedError(f"retired_workflows[{i}]: {exc}") from exc

    # -- stations: name-keyed documents with identity indirection. Mutable
    #    metadata updates on import (the re-export wins, like sources); a
    #    conflicting path_segment is loud (enclosure URLs are permanent);
    #    `retired: true` retires — the file never un-retires.
    for i, entry in enumerate(_need_list(data.get("stations"), "stations")):
        if not isinstance(entry, dict):
            raise SeedError(f"stations[{i}] must be an object")
        _need(entry.get("name"), f"stations[{i}].name")
        name = entry["name"]
        feed = entry.get("feed") or {}
        if not isinstance(feed, dict):
            raise SeedError(f"stations[{i}].feed must be an object")
        unknown = sorted(set(feed) - set(FEED_IDENTITY_KEYS))
        if unknown:
            raise SeedError(f"stations[{i}].feed: unknown keys "
                            f"{', '.join(unknown)}")
        workflow_ref = entry.get("workflow")
        if workflow_ref is not None and \
                (not isinstance(workflow_ref, str) or not workflow_ref.strip()):
            raise SeedError(f"stations[{i}].workflow must be a ref string")
        watchlist_id = None
        if entry.get("watchlist") is not None:
            watchlist = next((w for w in wrepo.list()
                              if w.name == entry["watchlist"]), None)
            if watchlist is None:
                raise SeedError(f"stations[{i}]: unknown watchlist "
                                f"'{entry['watchlist']}'")
            watchlist_id = watchlist.id
        existing = stations.get(name)
        try:
            if existing is None:
                stations.create(
                    name, actor=actor, via="seed",
                    description=entry.get("description"),
                    workflow_ref=workflow_ref or "default-morning",
                    watchlist_id=watchlist_id,
                    path_segment=entry.get("path_segment"),
                    feed_title=feed.get("title"),
                    feed_description=feed.get("description"),
                    feed_author=feed.get("author"),
                    feed_category=feed.get("category"),
                    feed_language=feed.get("language"),
                    feed_owner_email=feed.get("owner_email"))
                stats["stations_added"] += 1
            else:
                if (entry.get("path_segment") or None) \
                        != existing["path_segment"]:
                    raise SeedError(
                        f"stations[{i}]: '{name}' exists with path_segment "
                        f"{existing['path_segment']!r} — path segments are "
                        f"immutable (enclosure URLs are permanent)")
                mutable = (entry.get("description"),
                           workflow_ref or "default-morning",
                           watchlist_id,
                           feed.get("title"), feed.get("description"),
                           feed.get("author"), feed.get("category"),
                           feed.get("language"), feed.get("owner_email"))
                current = (existing["description"], existing["workflow_ref"],
                           existing["watchlist_id"], existing["feed_title"],
                           existing["feed_description"],
                           existing["feed_author"], existing["feed_category"],
                           existing["feed_language"],
                           existing["feed_owner_email"])
                if mutable != current:
                    stations.update_identity(
                        name, actor=actor, via="seed",
                        description=entry.get("description"),
                        workflow_ref=workflow_ref or "default-morning",
                        watchlist_id=watchlist_id,
                        feed_title=feed.get("title"),
                        feed_description=feed.get("description"),
                        feed_author=feed.get("author"),
                        feed_category=feed.get("category"),
                        feed_language=feed.get("language"),
                        feed_owner_email=feed.get("owner_email"))
                    stats["stations_updated"] += 1
                else:
                    stats["stations_present"] += 1
            if entry.get("retired"):
                stations.retire(name, actor=actor, via="seed")
        except CatalogError as exc:
            raise SeedError(f"stations[{i}]: {exc}") from exc
    return stats


def _need_list(value: Any, what: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SeedError(f"'{what}' must be a list")
    return value


def _need(value: Any, what: str) -> None:
    if not value or not isinstance(value, str):
        raise SeedError(f"'{what}' is missing or not a string")
