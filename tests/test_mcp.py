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

    from newsdesk.mcp_server import add_source, mcp, search_items

    result = asyncio.run(mcp.call_tool(
        "add_source", {"url": "https://example.invalid/feed.xml", "kind": "rss"}))
    payload = json.loads(result.content[0].text)
    assert payload["created"] is True

    # sync tool functions are callable directly (FastMCP-style wrappers too)
    found = json.loads(search_items("anything"))
    assert isinstance(found, list)
