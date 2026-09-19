"""Seed files: sources + watchlists as committed JSON.

The Actions runner checks out only the git repo — the collection state
(sources, watchlist terms) lives in NEWSDESK_HOME's database and never
rides along. A seed file is the versioned bridge: ``newsdesk seed
export`` locally, commit, ``newsdesk seed import`` at the top of every
fresh database (CI, a new machine).

Import is idempotent on the same identity keys the database itself
uses: sources on (url, kind), watchlists on name, terms on
(watchlist, term, kind). Re-importing yesterday's seed never
duplicates anything; fields you changed locally re-export and win on
the next commit.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from .storage.repo import SourceRepo, WatchlistRepo

FORMAT = 1


class SeedError(Exception):
    """Seed file is malformed or made-up; message is safe to log."""


def export_seed(session: Session) -> dict[str, Any]:
    """Snapshot every source and watchlist into a JSON-ready dict."""
    srepo, wrepo = SourceRepo(session), WatchlistRepo(session)
    source_key = {s.id: [s.url, s.kind] for s in srepo.list()}
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
    }


def import_seed(session: Session, data: dict[str, Any]) -> dict[str, int]:
    """Seed an (often empty) database from an exported dict, idempotently.

    Returns the added/present counts so the caller can report what
    actually happened. Never deletes: sources and terms that exist in
    the database but not in the file are left alone.
    """
    if not isinstance(data, dict):
        raise SeedError("seed file must be a JSON object")
    if data.get("format") != FORMAT:
        raise SeedError(f"unsupported seed format {data.get('format')!r} "
                        f"(expected {FORMAT}) — re-export with this version")
    srepo, wrepo = SourceRepo(session), WatchlistRepo(session)
    stats = {"sources_added": 0, "sources_present": 0,
             "watchlists_created": 0, "terms_added": 0, "terms_present": 0,
             "links_added": 0, "links_present": 0}

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
