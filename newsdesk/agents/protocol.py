"""Agent protocol: the small, harness-independent tool surface.

The intelligence layer is deliberately independent of any agent framework.
These tools are the contract; they are exposed as plain HTTP (see api.app)
and as MCP tools (see mcp_server) for harness addons — ZCode, Claude Code,
Codex, and friends all speak MCP. Thin adapters for OpenAI Agents SDK /
LangGraph / CrewAI / AutoGen wrap this same protocol; workflow state stays
in the Newsdesk database, never in harness-specific memory.
"""

from __future__ import annotations

from typing import Any

from ..core.models import SOURCE_KINDS

_KINDS_ARG = "string (" + "|".join(SOURCE_KINDS) + ")"

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "list_sources",
        "description": "List registered sources with fetch status.",
        "args": {"enabled_only": "boolean (optional)"},
        "status": "wired",
        "http": "GET /tools/list_sources",
    },
    {
        "name": "add_source",
        "description": "Register a new source to monitor; unknown kinds are rejected.",
        "args": {"url": "string (feed or page URL)",
                 "kind": _KINDS_ARG,
                 "title": "string (optional)", "publisher": "string (optional)"},
        "status": "wired",
        "http": "POST /sources",
    },
    {
        "name": "run_collection",
        "description": "Run one collection pass over all enabled sources (or a subset).",
        "args": {"source_ids": "array of ints (optional)"},
        "status": "wired",
        "http": "POST /tools/run_collection",
    },
    {
        "name": "search_items",
        "description": "Full-text search over collected items.",
        "args": {"query": "string", "limit": "int (optional, default 20)"},
        "status": "wired",
        "http": "GET /tools/search_items?query=...",
    },
    {
        "name": "get_item",
        "description": "Fetch one canonical item record with provenance.",
        "args": {"item_id": "string"},
        "status": "wired",
        "http": "GET /items/{item_id}",
    },
    {
        "name": "get_cluster",
        "description": "Fetch a deduplicated event cluster with all member sources.",
        "args": {"cluster_id": "string"},
        "status": "planned (M2)",
        "http": None,
    },
    {
        "name": "summarize_item",
        "description": "Summarize one item (text, transcript, visual notes combined); "
                       "extractive fallback when no LLM is configured.",
        "args": {"item_id": "string", "force": "boolean (optional)"},
        "status": "wired",
        "http": "POST /tools/summarize_item",
    },
    {
        "name": "digest_item",
        "description": "Watch a video item: transcript (captions or audio "
                       "transcription) + keyframe descriptions (vision) + grounded "
                       "summary. Long-running; downloads media.",
        "args": {"item_id": "string", "force": "boolean (optional)"},
        "status": "wired",
        "http": "POST /tools/digest_item",
    },
    {
        "name": "summarize_cluster",
        "description": "Evidence-linked summary of a cluster; every claim cites item IDs.",
        "args": {"cluster_id": "string", "format": "brief|detailed|timeline|comparison|raw"},
        "status": "planned (M3)",
        "http": None,
    },
    {
        "name": "create_digest",
        "description": "The daily briefing: AI-ranked summary of what is worth "
                       "following, grounded in collected items with drill-down IDs.",
        "args": {"hours": "int (optional, default 24)", "limit": "int (optional, default 30)"},
        "status": "wired",
        "http": "GET /tools/daily_digest",
    },
    {
        "name": "export_log",
        "description": "Export the immutable activity log (JSONL).",
        "args": {"since": "ISO timestamp (optional)", "limit": "int (optional)"},
        "status": "wired",
        "http": "GET /tools/export_log",
    },
]


def tool_index() -> dict[str, dict[str, Any]]:
    return {spec["name"]: spec for spec in TOOL_SPECS}
