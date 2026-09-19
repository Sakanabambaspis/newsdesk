"""Seed files: sources, watchlists, and workflows as committed JSON.

The Actions runner checks out only the git repo — the collection state
(sources, watchlist terms) and the workflow catalog live in NEWSDESK_HOME's
database and never ride along. A seed file is the versioned bridge:
``newsdesk seed export`` locally, commit, ``newsdesk seed import`` at the
top of every fresh database (CI, a new machine).

Import is idempotent on the same identity keys the database itself
uses: sources on (url, kind), watchlists on name, terms on
(watchlist, term, kind), workflow versions on (name, version).
Re-importing yesterday's seed never duplicates anything; fields you
changed locally re-export and win on the next commit.

Format 2 (workflow tickets 04/06) adds the catalog sections: ``workflows``
carries full descriptor documents — they self-describe ``name@version``,
so no identity indirection is needed within the section — and export
writes the full history so a fresh DB can serve stations pinned to old
versions. Import of a version that already exists is a no-op on
parsed-equal documents and a loud ``SeedError`` on different ones
(history was violated somewhere; never overwrite). Retirement applies
monotonically: ``retired_workflows`` names retire on import, and a stale
seed can never un-retire — un-retire stays a deliberate local action.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from .storage.repo import SourceRepo, WatchlistRepo
from .workflow.catalog import CatalogError, WorkflowCatalog
from .workflow.schema import DescriptorError, require_valid

FORMAT = 2


class SeedError(Exception):
    """Seed file is malformed or made-up; message is safe to log."""


def export_seed(session: Session) -> dict[str, Any]:
    """Snapshot every source, watchlist, and workflow version into a
    JSON-ready dict."""
    srepo, wrepo = SourceRepo(session), WatchlistRepo(session)
    source_key = {s.id: [s.url, s.kind] for s in srepo.list()}
    catalog = WorkflowCatalog(session)
    workflows: list[dict[str, Any]] = []
    retired: list[str] = []
    for entry in catalog.list():  # ordered by name
        workflows.extend(v["document"]
                         for v in catalog.versions(entry["name"]))
        if entry["retired_at"] is not None:
            retired.append(entry["name"])
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
    stats = {"sources_added": 0, "sources_present": 0,
             "watchlists_created": 0, "terms_added": 0, "terms_present": 0,
             "links_added": 0, "links_present": 0,
             "workflows_added": 0, "workflows_present": 0}

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
