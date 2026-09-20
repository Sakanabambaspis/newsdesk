"""The tool-surface sync test (wayfinder ticket 13 §1).

"Describe once" is enforced here, not promised: every registry
implementation in ``agents.tools`` is described in ``TOOL_SPECS``, every
``wired`` spec — legacy and W5 alike — is reachable on both surfaces (its
HTTP route exists, its MCP tool exists), ``planned`` names stay unplumbed
on both, no implementation takes an ``actor`` argument (a harness must
not be able to forge ``user``/``system``), every mutating implementation
declares ``via``, unknown names still degrade to the 501 stub, and the
engine's check library, the static binding map and the schema's closed
check set stay in lockstep. A red test here means the surfaces drifted —
fix the drift, not the test.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi.testclient import TestClient

from newsdesk.agents import tools
from newsdesk.agents.protocol import TOOL_SPECS, tool_index


@pytest.fixture
def client(settings):
    from newsdesk.api.app import create_app

    return TestClient(create_app(settings))


@pytest.fixture
def mcp_names():
    pytest.importorskip("mcp")
    from newsdesk.mcp_server import mcp

    return {t.name for t in asyncio.run(mcp.list_tools())}


def _http_routes(app) -> set[tuple[str, str]]:
    return {(method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())}


def test_registry_specs_appear_in_tool_specs_verbatim():
    specs = tool_index()
    for name, entry in tools.TOOL_REGISTRY.items():
        assert name in specs, f"tool '{name}' is implemented but not described"
        assert specs[name] == entry["spec"], \
            f"tool '{name}': TOOL_SPECS entry drifted from the registry"


def test_every_wired_spec_is_reachable_on_both_surfaces(client, mcp_names):
    routes = _http_routes(client.app)
    wired = [s for s in TOOL_SPECS if s["status"] == "wired"]
    assert len(wired) >= 29  # 9 legacy + 20 workflow-module tools
    for spec in wired:
        method, _, path = spec["http"].partition(" ")
        assert (method, path.split("?")[0]) in routes, \
            f"tool '{spec['name']}': no HTTP route {spec['http']}"
        assert spec["name"] in mcp_names, \
            f"tool '{spec['name']}': no MCP tool"


def test_planned_specs_stay_unplumbed(mcp_names):
    for spec in TOOL_SPECS:
        if spec["status"].startswith("planned"):
            assert spec["http"] is None
            assert spec["name"] not in mcp_names, \
                f"planned tool '{spec['name']}' has an MCP implementation"
    # ...and every wired spec names a concrete route (reachability is
    # pinned by test_every_wired_spec_is_reachable_on_both_surfaces)
    assert all(s["http"] for s in TOOL_SPECS if s["status"] == "wired")


def test_no_implementation_takes_an_actor_and_mutating_ones_take_via():
    for name, entry in tools.TOOL_REGISTRY.items():
        params = inspect.signature(entry["fn"]).parameters
        assert "actor" not in params, \
            f"tool '{name}' takes an actor argument — a harness could forge it"
        if entry["spec"]["mutating"]:
            assert "via" in params, \
                f"mutating tool '{name}' must accept the adapter's via origin"
        assert entry["spec"]["mutating"] == any(
            name.startswith(prefix) for prefix in
            ("create_", "retire_", "unretire_", "update_")), \
            f"tool '{name}': mutating flag disagrees with the verb"


def test_unknown_tool_names_degrade_to_the_501_stub(client):
    planned = next(s["name"] for s in TOOL_SPECS
                   if s["status"].startswith("planned"))
    for name in (planned, "definitely_not_a_tool"):
        assert client.get(f"/tools/{name}").status_code == 501
        assert client.post(f"/tools/{name}", json={}).status_code == 501


def test_check_library_bindings_and_schema_stay_in_lockstep():
    from newsdesk.workflow import bindings
    from newsdesk.workflow.engine import _CHECKS
    from newsdesk.workflow.schema import CHECK_NAMES

    assert set(_CHECKS) == set(bindings.CHECK_REQUIRES) == set(CHECK_NAMES), \
        "the check library, the static binding map and the schema's closed " \
        "set are three views of one library — they must not drift"
