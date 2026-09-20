"""MCP server tests: tool surface consistency + an in-process tool call."""

from __future__ import annotations

import asyncio
import json

import pytest

from newsdesk.agents.protocol import TOOL_SPECS


def test_mcp_tools_cover_wired_protocol():
    pytest.importorskip("mcp")
    from newsdesk.mcp_server import mcp

    names = {t.name for t in asyncio.run(mcp.list_tools())}
    wired = {spec["name"] for spec in TOOL_SPECS
             if spec["status"] == "wired" and spec["name"] != "add_source"}
    # add_source is also exposed; everything wired is present on the MCP surface
    assert wired <= names
    assert {"add_source", "digest_item"} <= names


def test_mcp_search_items_roundtrip(settings, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setenv("NEWSDESK_HOME", str(settings.home))
    monkeypatch.setenv("NEWSDESK_DB_URL", settings.database_url)

    from newsdesk.mcp_server import mcp, search_items

    result = asyncio.run(mcp.call_tool(
        "add_source", {"url": "https://example.invalid/feed.xml", "kind": "rss"}))
    payload = json.loads(result.content[0].text)
    assert payload["created"] is True

    # sync tool functions are callable directly (FastMCP-style wrappers too)
    found = json.loads(search_items("anything"))
    assert isinstance(found, list)


def test_mcp_workflow_tool_roundtrip_tags_the_actor(settings, monkeypatch):
    """The W5 tools through the MCP surface: one call, fresh session, and
    the mutation lands in the log as actor=agent, via=mcp (ticket 13 §6:
    no tool takes an actor argument, so a harness cannot forge one)."""
    pytest.importorskip("mcp")
    monkeypatch.setenv("NEWSDESK_HOME", str(settings.home))
    monkeypatch.setenv("NEWSDESK_DB_URL", settings.database_url)

    from newsdesk.mcp_server import mcp

    document = {
        "format_version": 1, "name": "mcp-made", "version": 1,
        "stages": [
            {"type": "collect"}, {"type": "select"},
            {"type": "compose", "name": "script", "params": {}},
            {"type": "render"}, {"type": "publish"}, {"type": "notify"},
        ],
    }
    result = asyncio.run(mcp.call_tool(
        "create_workflow", {"document": document}))
    payload = json.loads(result.content[0].text)
    assert payload == {"name": "mcp-made", "version": 1, "actor": "agent",
                       "via": "mcp",
                       "validation": {"schema": True, "bindings": True}}

    result = asyncio.run(mcp.call_tool("run_workflow", {"ref": "mcp-made"}))
    assert json.loads(result.content[0].text)["outcome"] == "dry_run"

    from newsdesk.storage.repo import LogRepo
    from newsdesk.storage.db import Database

    db = Database(Settings_lite(settings))
    with db.session() as session:
        entries = [(e.action, e.actor, e.detail)
                   for e in LogRepo(session).recent(limit=20)]
    assert ("workflow_created", "agent",
            {"workflow": "mcp-made", "via": "mcp"}) in entries
    assert ("workflow_version_created", "agent",
            {"workflow": "mcp-made", "via": "mcp", "version": 1}) in entries


def Settings_lite(settings):
    """The MCP server reads NEWSDESK_* env itself; tests only need the
    same home. Kept as a helper so the test reads top to bottom."""
    from newsdesk.config import Settings

    return Settings(home=settings.home, db_url=settings.db_url,
                    min_request_interval=0.0)
