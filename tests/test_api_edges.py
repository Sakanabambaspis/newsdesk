"""Edge-surface tests for the HTTP API: 404 paths, validation bounds, and
the tool stub route — the parts of newsdesk.api.app the flow tests skip.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from newsdesk.api.app import create_app


def test_toggle_missing_source_404(settings):
    client = TestClient(create_app(settings))
    assert client.patch("/sources/999?enabled=false").status_code == 404
    assert client.patch("/sources/999?enabled=true").json()["detail"] == \
        "source not found"


def test_add_source_rejects_unknown_kind_at_add_time(settings):
    """DESIGN stage-1 contract (audit C3, fixed): an invalid kind is a 422 at
    registration, never a deferred collect-time error."""
    client = TestClient(create_app(settings))
    response = client.post("/sources", json={"url": "https://x.example/f.xml",
                                             "kind": "time-machine"})
    assert response.status_code == 422
    assert "time-machine" in response.json()["detail"]


def test_watchlist_term_and_attach_404_paths(settings):
    client = TestClient(create_app(settings))
    term = client.post("/watchlists/4242/terms", json={"term": "x"})
    assert term.status_code == 404
    attach = client.post("/watchlists/4242/sources/1")
    assert attach.status_code == 404


def test_term_weight_validation_bounds(settings):
    client = TestClient(create_app(settings))
    wl = client.post("/watchlists", json={"name": "bounds"}).json()
    ok = client.post(f"/watchlists/{wl['id']}/terms",
                     json={"term": "grid", "weight": 10.0})
    assert ok.status_code == 201
    too_big = client.post(f"/watchlists/{wl['id']}/terms",
                          json={"term": "grid", "weight": 10.5})
    assert too_big.status_code == 422  # ge=0.0 le=10.0 constraint
    negative = client.post(f"/watchlists/{wl['id']}/terms",
                           json={"term": "grid", "weight": -0.1})
    assert negative.status_code == 422


def test_tool_stub_501_for_planned_tools(settings):
    client = TestClient(create_app(settings))
    for planned in ("get_cluster", "summarize_cluster"):
        response = client.get(f"/tools/{planned}")
        assert response.status_code == 501
        assert "planned" in response.json()["detail"]
        assert response.json()["detail"].endswith("see GET /tools")


def test_export_log_limit_param(settings, energy_feed):
    client = TestClient(create_app(settings))
    client.post("/sources", json={"url": energy_feed.as_uri()})
    client.post("/tools/run_collection", json={})
    one = client.get("/tools/export_log?limit=1").json()
    everything = client.get("/tools/export_log?limit=500").json()
    assert 1 <= len(one) < len(everything)
    assert all(set(e) == {"id", "ts", "actor", "action", "detail"}
               for e in everything)


def test_items_endpoint_limit_passthrough(settings, energy_feed):
    client = TestClient(create_app(settings))
    client.post("/sources", json={"url": energy_feed.as_uri()})
    client.post("/tools/run_collection", json={})
    assert len(client.get("/items?limit=2").json()) == 2
    assert len(client.get("/items").json()) == 3  # default 20, 3 available
    # query routes to FTS search, not recency
    hits = client.get("/items", params={"query": "podcast"}).json()
    assert len(hits) == 1 and "podcast" in hits[0]["content"]["title"].lower()
