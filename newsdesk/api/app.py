"""HTTP API: user-facing endpoints plus the agent tool surface.

Run locally:  newsdesk serve   (or uvicorn newsdesk.api.app:app)
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..agents.protocol import TOOL_SPECS
from ..config import Settings
from ..pipeline.runner import run_collection
from ..storage.db import Database
from ..storage.repo import (ItemRepo, LogRepo, SourceRepo, WatchlistRepo)


class SourceIn(BaseModel):
    url: str
    kind: str = "rss"
    title: str | None = None
    publisher: str | None = None
    fetch_interval_minutes: int = 30


class CollectIn(BaseModel):
    source_ids: list[int] | None = None


class SummarizeIn(BaseModel):
    item_id: str
    force: bool = False


class WatchlistIn(BaseModel):
    name: str
    description: str | None = None


class TermIn(BaseModel):
    term: str
    kind: str = "include"
    weight: float = Field(default=1.0, ge=0.0, le=10.0)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings)

    app = FastAPI(title="Newsdesk", version="0.1.0",
                  description="Watchlist-driven news collection agent")

    def session():
        return db.session()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "fts_enabled": db.fts_enabled}

    # -- sources ---------------------------------------------------------

    @app.get("/sources")
    def list_sources(enabled_only: bool = False) -> list[dict[str, Any]]:
        with session() as s:
            rows = SourceRepo(s).list(enabled_only=enabled_only)
            return [_source_out(r) for r in rows]

    @app.post("/sources", status_code=201)
    def add_source(payload: SourceIn) -> dict[str, Any]:
        with session() as s:
            try:
                source, created = SourceRepo(s).add(
                    payload.url, kind=payload.kind, title=payload.title,
                    publisher=payload.publisher,
                    fetch_interval_minutes=payload.fetch_interval_minutes,
                )
            except ValueError as exc:  # unknown kind: rejected at add time
                raise HTTPException(422, str(exc))
            if created:
                LogRepo(s).append("source_added", {"source_id": source.id, "url": source.url,
                                                   "kind": source.kind}, actor="user")
            return {**_source_out(source), "created": created}

    @app.patch("/sources/{source_id}")
    def toggle_source(source_id: int, enabled: bool) -> dict[str, Any]:
        with session() as s:
            source = SourceRepo(s).set_enabled(source_id, enabled)
            if not source:
                raise HTTPException(404, "source not found")
            return _source_out(source)

    # -- items ------------------------------------------------------------

    @app.get("/items")
    def items(query: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with session() as s:
            repo = ItemRepo(s)
            rows = repo.search(query, limit=limit) if query else repo.recent(limit=limit)
            return [r.to_canonical() for r in rows]

    @app.get("/items/{item_id}")
    def get_item(item_id: str) -> dict[str, Any]:
        with session() as s:
            row = ItemRepo(s).get(item_id)
            if not row:
                raise HTTPException(404, "item not found")
            return row.to_canonical()

    # -- watchlists -------------------------------------------------------

    @app.get("/watchlists")
    def list_watchlists() -> list[dict[str, Any]]:
        with session() as s:
            rows = WatchlistRepo(s).list()
            return [{"id": r.id, "name": r.name, "description": r.description} for r in rows]

    @app.post("/watchlists", status_code=201)
    def create_watchlist(payload: WatchlistIn) -> dict[str, Any]:
        with session() as s:
            wl = WatchlistRepo(s).create(payload.name, payload.description)
            LogRepo(s).append("watchlist_created", {"watchlist_id": wl.id, "name": wl.name},
                              actor="user")
            return {"id": wl.id, "name": wl.name, "description": wl.description}

    @app.post("/watchlists/{watchlist_id}/terms", status_code=201)
    def add_term(watchlist_id: int, payload: TermIn) -> dict[str, Any]:
        with session() as s:
            term = WatchlistRepo(s).add_term(watchlist_id, payload.term, payload.kind,
                                             payload.weight)
            if not term:
                raise HTTPException(404, "watchlist not found")
            return {"id": term.id, "watchlist_id": term.watchlist_id,
                    "term": term.term, "kind": term.kind, "weight": term.weight}

    @app.post("/watchlists/{watchlist_id}/sources/{source_id}", status_code=201)
    def attach_source(watchlist_id: int, source_id: int) -> dict[str, Any]:
        with session() as s:
            ok = WatchlistRepo(s).attach_source(watchlist_id, source_id)
            if not ok:
                raise HTTPException(404, "watchlist not found")
            return {"watchlist_id": watchlist_id, "source_id": source_id}

    # -- log --------------------------------------------------------------

    @app.get("/log")
    def log(limit: int = 50) -> list[dict[str, Any]]:
        with session() as s:
            return [e.to_json() for e in LogRepo(s).recent(limit=limit)]

    # -- agent tools (protocol surface) ------------------------------------

    @app.get("/tools")
    def tools() -> list[dict[str, Any]]:
        return TOOL_SPECS

    @app.get("/tools/list_sources")
    def tool_list_sources() -> list[dict[str, Any]]:
        return list_sources()

    @app.post("/tools/run_collection")
    def tool_run_collection(payload: CollectIn | None = None) -> dict[str, Any]:
        with session() as s:
            job = run_collection(s, settings, payload.source_ids if payload else None)
            return {"job_id": job.id, "status": job.status, "stats": job.stats}

    @app.get("/tools/search_items")
    def tool_search_items(query: str, limit: int = 20) -> list[dict[str, Any]]:
        return items(query=query, limit=limit)

    @app.get("/tools/daily_digest")
    def tool_daily_digest(hours: int = 24, limit: int = 30) -> dict[str, Any]:
        with session() as s:
            from ..pipeline.digest import build_daily_digest
            return build_daily_digest(s, settings, hours=hours, limit=limit)

    @app.post("/tools/summarize_item")
    def tool_summarize_item(payload: SummarizeIn) -> dict[str, Any]:
        with session() as s:
            from ..pipeline.summarize import summarize_item
            summary = summarize_item(s, settings, payload.item_id, force=payload.force)
        if summary.get("error") == "not_found":
            raise HTTPException(404, "item not found")
        return summary

    @app.post("/tools/digest_item")
    def tool_digest_item(payload: SummarizeIn) -> dict[str, Any]:
        with session() as s:
            from ..pipeline.summarize import digest_item
            report = digest_item(s, settings, payload.item_id, force=payload.force)
        if report.get("error") == "not_found":
            raise HTTPException(404, "item not found")
        return report

    @app.get("/tools/export_log")
    def tool_export_log(limit: int = 500) -> list[dict[str, Any]]:
        return log(limit=limit)

    @app.get("/tools/{tool_name}")
    def tool_stub(tool_name: str) -> dict[str, Any]:
        raise HTTPException(501, detail=f"tool '{tool_name}' is planned; see GET /tools")

    return app


def _source_out(source) -> dict[str, Any]:
    return {
        "id": source.id, "url": source.url, "kind": source.kind,
        "title": source.title, "publisher": source.publisher,
        "enabled": source.enabled, "fetch_interval_minutes": source.fetch_interval_minutes,
        "last_fetched_at": source.last_fetched_at.isoformat() if source.last_fetched_at else None,
        "last_status": source.last_status,
    }


app = create_app()
