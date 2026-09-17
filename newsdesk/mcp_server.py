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


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
