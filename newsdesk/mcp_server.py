"""MCP server: newsdesk as an addon for agent harnesses.

Exposes the same wired subset of the tool protocol (newsdesk.agents.protocol)
as Model Context Protocol tools over stdio, so ZCode, Claude Code, Codex, and
any MCP-capable harness drive one shared newsdesk database. Workflow state
stays in newsdesk; the harness holds no newsdesk-specific memory.

Run: ``newsdesk-mcp`` (console script) or ``newsdesk mcp``.
Config: inherits NEWSDESK_HOME / NEWSDESK_LLM_* environment variables.
"""

from __future__ import annotations

import json
from typing import Any

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .agents import tools
from .agents.protocol import TOOL_SPECS
from .config import Settings
from .pipeline.runner import run_collection
from .pipeline.summarize import digest_item, summarize_item
from .storage.db import Database
from .storage.repo import ItemRepo, LogRepo, SourceRepo

mcp = _Server(name="newsdesk",
              description="Watchlist-driven news collection agent "
                          "(provenance-first, evidence-linked summaries).")

_DESCRIPTIONS = {spec["name"]: spec["description"] for spec in TOOL_SPECS}


def _settings() -> Settings:
    settings = Settings.from_env()
    settings.ensure_dirs()
    return settings


def _run(fn) -> Any:
    """Open a fresh session per call and contain failures into tool results."""
    settings = _settings()
    db = Database(settings)
    try:
        with db.session() as session:
            return fn(session, settings)
    except Exception as exc:  # surface as data, never crash the server loop
        return {"error": str(exc)}
    finally:
        db.engine.dispose()  # per-call engine: release its connections


@mcp.tool(description=_DESCRIPTIONS["list_sources"])
def list_sources(enabled_only: bool = False) -> str:
    rows = _run(lambda s, _: SourceRepo(s).list(enabled_only=enabled_only))
    return json.dumps([
        {"id": r.id, "url": r.url, "kind": r.kind, "title": r.title,
         "publisher": r.publisher, "enabled": r.enabled,
         "last_status": r.last_status, "last_fetched_at":
             r.last_fetched_at.isoformat() if r.last_fetched_at else None}
        for r in rows
    ], ensure_ascii=False)


@mcp.tool(description=_DESCRIPTIONS["add_source"])
def add_source(url: str, kind: str = "rss", title: str | None = None,
               publisher: str | None = None) -> str:
    def op(session, _settings):
        source, created = SourceRepo(session).add(url, kind=kind, title=title,
                                                  publisher=publisher)
        if created:
            LogRepo(session).append("source_added", {"source_id": source.id, "url": url,
                                                     "kind": kind}, actor="user")
        return {"id": source.id, "created": created, "kind": source.kind}
    return json.dumps(_run(op), ensure_ascii=False)


@mcp.tool(name="run_collection", description=_DESCRIPTIONS["run_collection"])
def run_collection_tool(source_ids: list[int] | None = None) -> str:
    def op(session, settings):
        job = run_collection(session, settings, source_ids)
        return {"job_id": job.id, "status": job.status, "stats": job.stats}
    return json.dumps(_run(op), ensure_ascii=False)


@mcp.tool(description=_DESCRIPTIONS["search_items"])
def search_items(query: str, limit: int = 20) -> str:
    rows = _run(lambda s, _: ItemRepo(s).search(query, limit=limit))
    return json.dumps([r.to_canonical() for r in rows], ensure_ascii=False)


@mcp.tool(description=_DESCRIPTIONS["get_item"])
def get_item(item_id: str) -> str:
    row = _run(lambda s, _: ItemRepo(s).get(item_id))
    return json.dumps(row.to_canonical() if row else {"error": "not_found",
                                                      "item_id": item_id},
                      ensure_ascii=False)


@mcp.tool(name="summarize_item", description="Summarize a single item; uses its transcript and visual "
                      "description when present. Falls back to an extractive "
                      "summary when no LLM is configured.")
def summarize_item_tool(item_id: str, force: bool = False) -> str:
    return json.dumps(_run(lambda s, cfg: summarize_item(s, cfg, item_id, force=force)),
                      ensure_ascii=False)


@mcp.tool(name="digest_item", description="Watch and digest a video item: ensure transcript "
                      "(captions, or audio transcription if enabled), extract "
                      "and describe keyframes (needs ffmpeg + vision model), "
                      "then summarize. Long-running; downloads media.")
def digest_item_tool(item_id: str, force: bool = False) -> str:
    return json.dumps(_run(lambda s, cfg: digest_item(s, cfg, item_id, force=force)),
                      ensure_ascii=False)


@mcp.tool(name="create_digest", description="The daily briefing: AI-ranked "
                  "summary of what is worth following, grounded in collected "
                  "items with drill-down IDs.")
def create_digest(hours: int = 24, limit: int = 30) -> str:
    def op(session, settings):
        from .pipeline.digest import build_daily_digest
        return build_daily_digest(session, settings, hours=hours, limit=limit)
    return json.dumps(_run(op), ensure_ascii=False)


@mcp.tool(description=_DESCRIPTIONS["export_log"])
def export_log(limit: int = 500) -> str:
    rows = _run(lambda s, _: LogRepo(s).recent(limit=limit))
    return json.dumps([e.to_json() for e in rows], ensure_ascii=False)


# -- workflow-module tools (W5; thin adapters over agents.tools) --------------
#
# The implementations in agents.tools carry the specs and do the work
# (actor="agent", via="mcp" here); these wrappers only parse typed
# arguments, delegate, and serialize. Failures are contained by _run into
# {"error": ...} — never a crashed server loop, never a stack trace.

@mcp.tool(name="list_workflows", description=_DESCRIPTIONS["list_workflows"])
def list_workflows_tool(query: str | None = None,
                        include_retired: bool = False, limit: int = 50) -> str:
    return json.dumps(_run(lambda s, cfg: tools.list_workflows(
        s, cfg, query=query, include_retired=include_retired, limit=limit)),
        ensure_ascii=False)


@mcp.tool(name="get_workflow", description=_DESCRIPTIONS["get_workflow"])
def get_workflow_tool(ref: str, version: int | None = None) -> str:
    return json.dumps(_run(lambda s, cfg: tools.get_workflow(s, cfg, ref,
                                                             version)),
                      ensure_ascii=False)


@mcp.tool(name="create_workflow", description=_DESCRIPTIONS["create_workflow"])
def create_workflow_tool(document: dict, dry_run: bool = False) -> str:
    return json.dumps(_run(lambda s, cfg: tools.create_workflow(
        s, cfg, document, dry_run, via="mcp")), ensure_ascii=False)


@mcp.tool(name="diff_workflow", description=_DESCRIPTIONS["diff_workflow"])
def diff_workflow_tool(name: str, from_version: int, to_version: int) -> str:
    return json.dumps(_run(lambda s, cfg: tools.diff_workflow(
        s, cfg, name, from_version, to_version)), ensure_ascii=False)


@mcp.tool(name="retire_workflow", description=_DESCRIPTIONS["retire_workflow"])
def retire_workflow_tool(name: str) -> str:
    return json.dumps(_run(lambda s, cfg: tools.retire_workflow(s, cfg, name,
                                                                via="mcp")),
                      ensure_ascii=False)


@mcp.tool(name="unretire_workflow",
          description=_DESCRIPTIONS["unretire_workflow"])
def unretire_workflow_tool(name: str) -> str:
    return json.dumps(_run(lambda s, cfg: tools.unretire_workflow(
        s, cfg, name, via="mcp")), ensure_ascii=False)


@mcp.tool(name="list_rubrics", description=_DESCRIPTIONS["list_rubrics"])
def list_rubrics_tool(limit: int = 50) -> str:
    return json.dumps(_run(lambda s, cfg: tools.list_rubrics(s, cfg, limit)),
                      ensure_ascii=False)


@mcp.tool(name="get_rubric", description=_DESCRIPTIONS["get_rubric"])
def get_rubric_tool(ref: str, version: int | None = None) -> str:
    return json.dumps(_run(lambda s, cfg: tools.get_rubric(s, cfg, ref,
                                                           version)),
                      ensure_ascii=False)


@mcp.tool(name="create_rubric", description=_DESCRIPTIONS["create_rubric"])
def create_rubric_tool(document: dict) -> str:
    return json.dumps(_run(lambda s, cfg: tools.create_rubric(s, cfg,
                                                              document,
                                                              via="mcp")),
                      ensure_ascii=False)


@mcp.tool(name="diff_rubric", description=_DESCRIPTIONS["diff_rubric"])
def diff_rubric_tool(name: str, from_version: int, to_version: int) -> str:
    return json.dumps(_run(lambda s, cfg: tools.diff_rubric(
        s, cfg, name, from_version, to_version)), ensure_ascii=False)


@mcp.tool(name="score_preview", description=_DESCRIPTIONS["score_preview"])
def score_preview_tool(rubric: dict | str, limit: int = 10,
                       hours: float = 24) -> str:
    return json.dumps(_run(lambda s, cfg: tools.score_preview(
        s, cfg, rubric, limit, hours)), ensure_ascii=False)


@mcp.tool(name="retire_rubric", description=_DESCRIPTIONS["retire_rubric"])
def retire_rubric_tool(name: str) -> str:
    return json.dumps(_run(lambda s, cfg: tools.retire_rubric(s, cfg, name,
                                                              via="mcp")),
                      ensure_ascii=False)


@mcp.tool(name="unretire_rubric",
          description=_DESCRIPTIONS["unretire_rubric"])
def unretire_rubric_tool(name: str) -> str:
    return json.dumps(_run(lambda s, cfg: tools.unretire_rubric(
        s, cfg, name, via="mcp")), ensure_ascii=False)


@mcp.tool(name="list_stations", description=_DESCRIPTIONS["list_stations"])
def list_stations_tool(include_retired: bool = True) -> str:
    return json.dumps(_run(lambda s, cfg: tools.list_stations(
        s, cfg, include_retired=include_retired)), ensure_ascii=False)


@mcp.tool(name="get_station", description=_DESCRIPTIONS["get_station"])
def get_station_tool(name: str) -> str:
    return json.dumps(_run(lambda s, cfg: tools.get_station(s, cfg, name)),
                      ensure_ascii=False)


@mcp.tool(name="create_station", description=_DESCRIPTIONS["create_station"])
def create_station_tool(name: str, workflow: str,
                        watchlist: int | None = None,
                        path_segment: str | None = None,
                        description: str | None = None,
                        feed: dict | None = None) -> str:
    return json.dumps(_run(lambda s, cfg: tools.create_station(
        s, cfg, name, workflow, watchlist, path_segment, description, feed,
        via="mcp")), ensure_ascii=False)


@mcp.tool(name="update_station", description=_DESCRIPTIONS["update_station"])
def update_station_tool(name: str, workflow: str,
                        watchlist: int | None = None,
                        description: str | None = None,
                        feed: dict | None = None) -> str:
    return json.dumps(_run(lambda s, cfg: tools.update_station(
        s, cfg, name, workflow, watchlist, description, feed, via="mcp")),
        ensure_ascii=False)


@mcp.tool(name="retire_station", description=_DESCRIPTIONS["retire_station"])
def retire_station_tool(name: str) -> str:
    return json.dumps(_run(lambda s, cfg: tools.retire_station(s, cfg, name,
                                                               via="mcp")),
                      ensure_ascii=False)


@mcp.tool(name="unretire_station",
          description=_DESCRIPTIONS["unretire_station"])
def unretire_station_tool(name: str) -> str:
    return json.dumps(_run(lambda s, cfg: tools.unretire_station(
        s, cfg, name, via="mcp")), ensure_ascii=False)


@mcp.tool(name="run_workflow", description=_DESCRIPTIONS["run_workflow"])
def run_workflow_tool(ref: str, station: str | None = None,
                      date: str | None = None, verbose: bool = False) -> str:
    return json.dumps(_run(lambda s, cfg: tools.run_workflow(
        s, cfg, ref, station, date, verbose)), ensure_ascii=False)


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
