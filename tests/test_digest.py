"""Daily digest tests: selection, grouping, LLM path, fallback, HTTP surface."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from fastapi.testclient import TestClient

from newsdesk.api.app import create_app
from newsdesk.llm.base import BaseLLMAdapter
from newsdesk.pipeline.digest import build_daily_digest, render_markdown
from newsdesk.storage.repo import ItemRepo, LogRepo, SourceRepo, WatchlistRepo
from tests.conftest import make_canonical_item


def _mk_item(session, source, n: int, *, title: str, text: str,
             age_hours: float, relevance: float | None = None) -> str:
    when = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    item = make_canonical_item(
        url=f"https://x.example/{n}", title=title, text=text,
        id=f"item_digesttest{n:014d}",
        timestamps={"published_at": when.isoformat(),
                    "retrieved_at": when.isoformat()},
        analysis={"relevance": relevance},
        provenance={"content_hash": f"h{n}"},
    )
    outcome, row = ItemRepo(session).upsert(item, source_id=source.id)
    assert outcome == "created"
    return row.id


class FakeDigestAdapter(BaseLLMAdapter):
    name = "fake"

    def __init__(self, reply: dict):
        self.reply = reply
        self.seen: list[str] = []

    def complete(self, system, user, *, max_tokens=1200, temperature=0.2) -> str:
        self.seen.append(system + "\n" + user)
        return json.dumps(self.reply)


@pytest.fixture
def digest_world(session):
    source, _ = SourceRepo(session).add("https://feeds.example/x.xml", kind="rss")
    WatchlistRepo(session).create("dirs")
    for t in ("agents", "reasoning", "diffusion"):
        WatchlistRepo(session).add_term(1, t)
    ids = {
        "a1": _mk_item(session, source, 1, title="Agent frameworks mature",
                       text="New agents tooling and agents benchmarks arrive.",
                       age_hours=3, relevance=0.5),
        "a2": _mk_item(session, source, 2, title="Agents in production",
                       text="Agentic deployments scale; agents everywhere.",
                       age_hours=5, relevance=0.4),
        "r1": _mk_item(session, source, 3, title="Reasoning models improve",
                       text="Chain-of-thought reasoning gains; test-time wins.",
                       age_hours=2, relevance=0.9),
        "old": _mk_item(session, source, 4, title="Old agent news",
                        text="agent from last week", age_hours=72, relevance=1.0),
        "unmatched": _mk_item(session, source, 5, title="Cats",
                              text="A cat video.", age_hours=1, relevance=0.0),
    }
    return source, ids


def test_digest_selection_window_and_ranking(session, settings, digest_world):
    _, ids = digest_world
    digest = build_daily_digest(session, settings, hours=24, adapter=FakeDigestAdapter({}))
    window_ids = [i["id"] for i in digest["items"]]
    assert ids["a1"] in window_ids and ids["r1"] in window_ids
    assert ids["old"] not in window_ids          # outside the 24h window
    assert window_ids.index(ids["r1"]) < window_ids.index(ids["a2"])  # ranked
    # items considered capped at 30
    assert digest["items_considered"] <= 30


def test_digest_fallback_groups_by_watchlist_term(session, settings, digest_world):
    _, ids = digest_world
    digest = build_daily_digest(session, settings, hours=24,
                                adapter=FakeDigestAdapter({"error": "llm_not_configured"}))
    assert digest["method"] == "extractive"
    assert "no LLM" in digest["overview"]
    themes = {s["theme"].split(" (")[0]: s for s in digest["worth_following"]}
    # "agents" outranks "agentic"? both match; highest-weight matched term keys the group
    agents_items = [s for name, s in themes.items() if "agent" in name]
    assert agents_items
    flat = [i for s in agents_items for i in s["item_ids"]]
    assert ids["a1"] in flat and ids["a2"] in flat


def test_digest_llm_path_guards_citations(session, settings, digest_world):
    _, ids = digest_world
    reply = {
        "overview": "Agents and reasoning dominated.",
        "worth_following": [
            {"theme": "Agents", "why": "Maturity.", "item_ids": [ids["a1"], ids["a2"]]},
            {"theme": "Hallucinated", "why": "?", "item_ids": ["item_doesnotexist"]},
        ],
        "also_noteworthy": [],
        "noise": "Cats.",
    }
    adapter = FakeDigestAdapter(reply)
    digest = build_daily_digest(session, settings, hours=24, adapter=adapter)
    assert digest["method"] == "llm:fake"
    assert "UNTRUSTED DATA" in adapter.seen[0]  # prompt keeps untrusted framing
    themes = {s["theme"]: s for s in digest["worth_following"]}
    assert themes["Agents"]["item_ids"] == [ids["a1"], ids["a2"]]
    assert themes["Hallucinated"]["item_ids"] == []  # invalid citation dropped
    log = [e for e in LogRepo(session).recent(limit=5) if e.action == "daily_digest_built"]
    assert log and log[0].detail["method"] == "llm:fake"


def test_render_markdown_includes_drilldown(session, settings, digest_world):
    _, ids = digest_world
    digest = build_daily_digest(session, settings, hours=24)
    md = render_markdown(digest)
    assert md.startswith("# Newsdesk briefing")
    assert "## Worth following" in md
    assert f"newsdesk summarize {ids['a1']}" in md


def test_digest_http_endpoint(session, settings, digest_world):
    client = TestClient(create_app(settings))
    tools = {t["name"]: t for t in client.get("/tools").json()}
    assert tools["create_digest"]["status"] == "wired"
    resp = client.get("/tools/daily_digest?hours=24")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_hours"] == 24 and "items" in body
    assert body["worth_following"]
