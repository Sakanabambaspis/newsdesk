"""HTTP API: user-facing endpoints plus the agent tool surface.

Run locally:  newsdesk serve   (or uvicorn newsdesk.api.app:app)

The tool routes are thin adapters over the host-neutral implementations in
``agents.tools`` (tickets 13/14): parse, call, return. Validation, actor
tagging (``actor="agent"``, ``via="http"``) and the size etiquette live in
the implementations; failures arrive here as raised exceptions with
human-written messages and leave as 422s.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..agents.protocol import TOOL_SPECS
from ..agents.tools import (create_rubric, create_station, create_workflow,
                            diff_rubric, diff_workflow, get_rubric,
                            get_station, get_workflow, list_rubrics,
                            list_stations, list_workflows, retire_rubric,
                            retire_station, retire_workflow,
                            run_workflow as run_workflow_tool, score_preview,
                            unretire_rubric, unretire_station,
                            unretire_workflow, update_station)
from ..config import Settings
from ..pipeline.runner import run_collection
from ..storage.db import Database
from ..storage.repo import (ItemRepo, LogRepo, SourceRepo, WatchlistRepo)
from ..workflow.catalog import CatalogError
from ..workflow.engine import WorkflowRunError
from ..workflow.rubric import RubricError
from ..workflow.schema import DescriptorError


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


class DocumentIn(BaseModel):
    document: dict[str, Any]


class CreateWorkflowIn(BaseModel):
    document: dict[str, Any]
    dry_run: bool = False


class DiffIn(BaseModel):
    name: str
    from_version: int
    to_version: int


class NameIn(BaseModel):
    name: str


class ScorePreviewIn(BaseModel):
    rubric: dict[str, Any] | str
    limit: int = 10
    hours: float = 24


class CreateStationIn(BaseModel):
    name: str
    workflow: str
    watchlist: int | None = None
    path_segment: str | None = None
    description: str | None = None
    feed: dict[str, Any] | None = None


class UpdateStationIn(BaseModel):
    name: str
    workflow: str
    watchlist: int | None = None
    description: str | None = None
    feed: dict[str, Any] | None = None


class RunWorkflowIn(BaseModel):
    ref: str
    station: str | None = None
    date: str | None = None
    verbose: bool = False


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

    # -- workflow-module tools (W5; thin adapters over agents.tools) -------

    def call(fn, *args, **kwargs) -> dict[str, Any]:
        """One tool call with newsdesk's error containment: the
        implementations raise loud, human-written failures; the HTTP
        surface renders them as 422s, never a stack trace."""
        try:
            return fn(*args, **kwargs)
        except (CatalogError, WorkflowRunError, DescriptorError,
                RubricError, ValueError) as exc:
            raise HTTPException(422, str(exc))

    @app.get("/tools/list_workflows")
    def tool_list_workflows(query: str | None = None,
                            include_retired: bool = False,
                            limit: int = 50) -> dict[str, Any]:
        with session() as s:
            return call(list_workflows, s, settings, query=query,
                        include_retired=include_retired, limit=limit)

    @app.get("/tools/get_workflow")
    def tool_get_workflow(ref: str, version: int | None = None) \
            -> dict[str, Any]:
        with session() as s:
            return call(get_workflow, s, settings, ref, version)

    @app.post("/tools/create_workflow")
    def tool_create_workflow(payload: CreateWorkflowIn) -> dict[str, Any]:
        with session() as s:
            return call(create_workflow, s, settings, payload.document,
                        payload.dry_run, via="http")

    @app.post("/tools/diff_workflow")
    def tool_diff_workflow(payload: DiffIn) -> dict[str, Any]:
        with session() as s:
            return call(diff_workflow, s, settings, payload.name,
                        payload.from_version, payload.to_version)

    @app.post("/tools/retire_workflow")
    def tool_retire_workflow(payload: NameIn) -> dict[str, Any]:
        with session() as s:
            return call(retire_workflow, s, settings, payload.name,
                        via="http")

    @app.post("/tools/unretire_workflow")
    def tool_unretire_workflow(payload: NameIn) -> dict[str, Any]:
        with session() as s:
            return call(unretire_workflow, s, settings, payload.name,
                        via="http")

    @app.get("/tools/list_rubrics")
    def tool_list_rubrics(limit: int = 50) -> dict[str, Any]:
        with session() as s:
            return call(list_rubrics, s, settings, limit)

    @app.get("/tools/get_rubric")
    def tool_get_rubric(ref: str, version: int | None = None) \
            -> dict[str, Any]:
        with session() as s:
            return call(get_rubric, s, settings, ref, version)

    @app.post("/tools/create_rubric")
    def tool_create_rubric(payload: DocumentIn) -> dict[str, Any]:
        with session() as s:
            return call(create_rubric, s, settings, payload.document,
                        via="http")

    @app.post("/tools/diff_rubric")
    def tool_diff_rubric(payload: DiffIn) -> dict[str, Any]:
        with session() as s:
            return call(diff_rubric, s, settings, payload.name,
                        payload.from_version, payload.to_version)

    @app.post("/tools/score_preview")
    def tool_score_preview(payload: ScorePreviewIn) -> dict[str, Any]:
        with session() as s:
            return call(score_preview, s, settings, payload.rubric,
                        payload.limit, payload.hours)

    @app.post("/tools/retire_rubric")
    def tool_retire_rubric(payload: NameIn) -> dict[str, Any]:
        with session() as s:
            return call(retire_rubric, s, settings, payload.name,
                        via="http")

    @app.post("/tools/unretire_rubric")
    def tool_unretire_rubric(payload: NameIn) -> dict[str, Any]:
        with session() as s:
            return call(unretire_rubric, s, settings, payload.name,
                        via="http")

    @app.get("/tools/list_stations")
    def tool_list_stations(include_retired: bool = True) -> dict[str, Any]:
        with session() as s:
            return call(list_stations, s, settings, include_retired)

    @app.get("/tools/get_station")
    def tool_get_station(name: str) -> dict[str, Any]:
        with session() as s:
            return call(get_station, s, settings, name)

    @app.post("/tools/create_station")
    def tool_create_station(payload: CreateStationIn) -> dict[str, Any]:
        with session() as s:
            return call(create_station, s, settings, payload.name,
                        payload.workflow, payload.watchlist,
                        payload.path_segment, payload.description,
                        payload.feed, via="http")

    @app.post("/tools/update_station")
    def tool_update_station(payload: UpdateStationIn) -> dict[str, Any]:
        with session() as s:
            return call(update_station, s, settings, payload.name,
                        payload.workflow, payload.watchlist,
                        payload.description, payload.feed, via="http")

    @app.post("/tools/retire_station")
    def tool_retire_station(payload: NameIn) -> dict[str, Any]:
        with session() as s:
            return call(retire_station, s, settings, payload.name,
                        via="http")

    @app.post("/tools/unretire_station")
    def tool_unretire_station(payload: NameIn) -> dict[str, Any]:
        with session() as s:
            return call(unretire_station, s, settings, payload.name,
                        via="http")

    @app.post("/tools/run_workflow")
    def tool_run_workflow(payload: RunWorkflowIn) -> dict[str, Any]:
        with session() as s:
            return call(run_workflow_tool, s, settings, payload.ref,
                        payload.station, payload.date, payload.verbose)

    @app.get("/tools/{tool_name}")
    def tool_stub(tool_name: str) -> dict[str, Any]:
        raise HTTPException(501, detail=f"tool '{tool_name}' is planned; see GET /tools")

    @app.post("/tools/{tool_name}")
    def tool_stub_post(tool_name: str) -> dict[str, Any]:
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
