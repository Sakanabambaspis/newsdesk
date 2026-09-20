"""API + agent-tool surface smoke tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from newsdesk.api.app import create_app
from newsdesk.core.ids import item_id_for


def test_health_and_tools(settings):
    client = TestClient(create_app(settings))
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["fts_enabled"] is True

    tools = client.get("/tools").json()
    names = [t["name"] for t in tools]
    assert len(tools) == 31  # 11 collection tools + 20 W5 workflow-module tools
    assert names[:4] == ["list_sources", "add_source", "run_collection", "search_items"]
    by_name = {t["name"]: t for t in tools}
    assert by_name["add_source"]["http"] == "POST /sources"
    assert by_name["get_cluster"]["status"].startswith("planned")
    assert by_name["summarize_item"]["status"] == "wired"
    assert by_name["digest_item"]["http"] == "POST /tools/digest_item"
    assert "arxiv" in by_name["add_source"]["args"]["kind"]
    # the W5 tools ride the same list, generated from their registry
    assert by_name["run_workflow"]["mutating"] is False  # dry-run only
    assert by_name["create_workflow"]["mutating"] is True
    assert names[-1] == "run_workflow"


def test_api_collect_and_search_flow(settings, energy_feed):
    client = TestClient(create_app(settings))

    created = client.post("/sources", json={"url": energy_feed.as_uri()})
    assert created.status_code == 201
    assert created.json()["created"] is True

    duplicate_add = client.post("/sources", json={"url": energy_feed.as_uri()})
    assert duplicate_add.json()["created"] is False

    job = client.post("/tools/run_collection", json={}).json()
    assert job["status"] == "done"
    assert job["stats"]["totals"]["created"] == 3

    hits = client.get("/items", params={"query": "heat wave strains"}).json()
    assert len(hits) == 1
    assert "grid-emergency" in hits[0]["source"]["url"]
    assert hits[0]["provenance"]["extraction_method"] == "rss"

    wind_id = item_id_for("https://energy.example.com/stories/offshore-wind-auction")
    one = client.get(f"/items/{wind_id}")
    assert one.status_code == 200
    assert one.json()["source"]["publisher"] == "Example Energy Desk"
    assert client.get("/items/item_missing").status_code == 404

    sources = client.get("/tools/list_sources").json()
    assert sources[0]["last_status"] == "ok"

    log = client.get("/tools/export_log").json()
    assert any(e["action"] == "item_created" for e in log)

    stub = client.get("/tools/get_cluster")
    assert stub.status_code == 501


def test_watchlist_flow(settings):
    client = TestClient(create_app(settings))
    wl = client.post("/watchlists", json={"name": "energy-policy"}).json()
    term = client.post(f"/watchlists/{wl['id']}/terms",
                       json={"term": "Grid", "kind": "include", "weight": 2.0}).json()
    assert term["term"] == "grid"  # terms are normalized to lowercase

    source = client.post("/sources", json={"url": "https://example.com/feed.xml"}).json()
    attached = client.post(f"/watchlists/{wl['id']}/sources/{source['id']}")
    assert attached.status_code == 201

    assert client.get("/watchlists").json()[0]["name"] == "energy-policy"


def test_summarize_item_endpoint(settings, energy_feed):
    client = TestClient(create_app(settings))
    client.post("/sources", json={"url": energy_feed.as_uri()})
    client.post("/tools/run_collection", json={})
    item_id = client.get("/items?limit=1").json()[0]["id"]

    summary = client.post("/tools/summarize_item", json={"item_id": item_id}).json()
    assert summary["method"] == "extractive"  # no LLM configured in tests
    assert summary["headline"]

    missing = client.post("/tools/summarize_item", json={"item_id": "item_nope"})
    assert missing.status_code == 404
