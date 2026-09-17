"""ADR 0001 verdict pass + MaterialPack: guard, fallback, truncation policy."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from newsdesk.llm.base import BaseLLMAdapter
from newsdesk.pipeline.digest import build_daily_digest, render_markdown
from newsdesk.pipeline.material import build_material_pack
from newsdesk.storage.repo import ItemRepo, LogRepo, SourceRepo, WatchlistRepo
from tests.conftest import make_canonical_item


def _mk_item(session, source, n: int, *, title: str, text: str,
             age_hours: float, relevance: float | None = None) -> str:
    when = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    item = make_canonical_item(
        url=f"https://x.example/{n}", title=title, text=text,
        id=f"item_verdicttest{n:014d}",
        timestamps={"published_at": when.isoformat(),
                    "retrieved_at": when.isoformat()},
        analysis={"relevance": relevance},
        provenance={"content_hash": f"h{n}"},
    )
    outcome, row = ItemRepo(session).upsert(item, source_id=source.id)
    assert outcome == "created"
    return row.id


class CannedAdapter(BaseLLMAdapter):
    """complete() replays a canned string; used for adapter-contract tests."""

    def __init__(self, raw: str):
        self.raw = raw

    def complete(self, system, user, *, max_tokens=1200, temperature=0.2) -> str:
        return self.raw


class FakeVerdictAdapter(BaseLLMAdapter):
    """Prose via ``reply``; verdict pass returns ``verdicts`` or an error."""

    name = "fake"

    def __init__(self, reply: dict, verdicts: list | None = None,
                 verdict_error: str | None = None):
        self.reply = reply
        self.verdicts = verdicts
        self.verdict_error = verdict_error
        self.verdict_calls = 0

    def complete(self, system, user, *, max_tokens=1200, temperature=0.2) -> str:
        return json.dumps(self.reply)

    def classify_verdicts(self, items):
        self.verdict_calls += 1
        if self.verdict_error:
            return {"error": self.verdict_error}
        return {"verdicts": self.verdicts or []}


LLM_PROSE = {"overview": "Agents and reasoning dominated.",
             "worth_following": [{"theme": "Agents", "why": "Maturity.",
                                  "item_ids": []}],
             "also_noteworthy": [], "noise": ""}


@pytest.fixture
def digest_world(session):
    source, _ = SourceRepo(session).add("https://feeds.example/x.xml", kind="rss")
    WatchlistRepo(session).create("dirs")
    for t in ("agents", "reasoning"):
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
        "cats": _mk_item(session, source, 5, title="Cats",
                         text="A cat video.", age_hours=1, relevance=0.0),
    }
    return source, ids


def test_verdicts_attached_filtered_and_logged(session, settings, digest_world):
    _, ids = digest_world
    adapter = FakeVerdictAdapter(LLM_PROSE, verdicts=[
        {"id": ids["a1"], "verdict": "technical", "reason": "novel agent harness"},
        {"id": ids["r1"], "verdict": "technical", "reason": "test-time method"},
        {"id": ids["a2"], "verdict": "hype", "reason": "funding round noise"},
        {"id": ids["cats"], "verdict": "tangential", "reason": "cats"},
    ])
    digest = build_daily_digest(session, settings, hours=24, adapter=adapter)

    assert adapter.verdict_calls == 1
    assert digest["verdict_method"] == "llm:fake"
    cards = {c["id"]: c for c in digest["items"]}
    assert cards[ids["a1"]]["verdict"] == "technical"
    assert cards[ids["a2"]]["verdict"] == "hype"
    assert cards[ids["a2"]]["verdict_reason"] == "funding round noise"
    # hype stays visible in the digest; only the pack filters it out
    pack = digest["material_pack"]
    assert pack["verdict_filter"] == "technical"
    assert pack["verdict_counts"] == {"technical": 2, "hype": 1, "tangential": 1}
    assert pack["after_filter"] == 2 and pack["candidates"] == 4

    log = [e for e in LogRepo(session).recent(limit=5)
           if e.action == "daily_digest_built"][0]
    assert log.detail["verdict_method"] == "llm:fake"
    assert log.detail["verdict_counts"]["technical"] == 2
    logged = {v["id"]: v for v in log.detail["verdicts"]}
    assert logged[ids["a2"]] == {"id": ids["a2"], "verdict": "hype",
                                 "reason": "funding round noise"}


def test_verdict_grounding_guard_drops_unknown_ids(session, settings, digest_world):
    _, ids = digest_world
    adapter = FakeVerdictAdapter(LLM_PROSE, verdicts=[
        {"id": ids["a1"], "verdict": "technical", "reason": "real"},
        {"id": "item_hallucinated", "verdict": "technical", "reason": "not given"},
    ])
    digest = build_daily_digest(session, settings, hours=24, adapter=adapter)
    cards = {c["id"]: c for c in digest["items"]}
    assert cards[ids["a1"]]["verdict"] == "technical"
    for card in cards.values():
        if card["id"] != ids["a1"]:
            assert card["verdict"] is None and card["verdict_reason"] is None
    assert digest["material_pack"]["verdict_counts"]["technical"] == 1
    log = [e for e in LogRepo(session).recent(limit=5)
           if e.action == "daily_digest_built"][0]
    assert len(log.detail["verdicts"]) == 1


def test_unparseable_or_failed_verdicts_skip_labeled(session, settings, digest_world):
    _, ids = digest_world
    digest = build_daily_digest(session, settings, hours=24, adapter=FakeVerdictAdapter(
        LLM_PROSE, verdict_error="unparseable_model_output"))
    assert digest["verdict_method"] == "skipped:unparseable_model_output"
    assert all(c["verdict"] is None for c in digest["items"])
    # prose still landed; the pack falls back to relevance order, labeled
    assert digest["method"] == "llm:fake"
    assert digest["material_pack"]["verdict_filter"] == "skipped"
    assert "skipped:unparseable_model_output" in render_markdown(digest)


def test_no_llm_skips_verdicts_but_builds(session, settings, digest_world):
    _, ids = digest_world
    digest = build_daily_digest(session, settings, hours=24, adapter=FakeVerdictAdapter(
        {"error": "llm_not_configured"}, verdict_error="llm_not_configured"))
    assert digest["method"] == "extractive"
    assert digest["verdict_method"] == "skipped:llm_not_configured"
    assert all(c["verdict"] is None for c in digest["items"])
    assert "skipped:llm_not_configured" in render_markdown(digest)


def test_short_and_empty_days_degrade_gracefully(session, settings):
    source, _ = SourceRepo(session).add("https://feeds.example/y.xml", kind="rss")
    WatchlistRepo(session).create("dirs")
    only = _mk_item(session, source, 9, title="One lonely item",
                    text="single technical win", age_hours=1, relevance=0.7)
    adapter = FakeVerdictAdapter(LLM_PROSE, verdicts=[
        {"id": only, "verdict": "technical", "reason": "the one"}])
    digest = build_daily_digest(session, settings, hours=24, adapter=adapter)
    assert digest["verdict_method"] == "llm:fake"
    assert digest["material_pack"]["in_pack"] == 1

    empty = build_daily_digest(session, settings, hours=1,
                               adapter=FakeVerdictAdapter(LLM_PROSE))
    assert empty["verdict_method"] == "skipped:no_items"
    assert empty["material_pack"]["in_pack"] == 0


def test_adapter_classify_verdicts_enforces_contract():
    """Shape, enum, reason, and id grounding are enforced in the adapter."""
    items = [{"id": "item_1", "title": "t", "snippet": "s",
              "publisher": "p", "relevance": 1.0}]
    raw = json.dumps({"verdicts": [
        {"id": "item_1", "verdict": "technical", "reason": "ok"},
        {"id": "item_1", "verdict": "seo", "reason": "unknown value"},
        {"id": "item_1", "verdict": "hype", "reason": "   "},
        {"id": "item_never_given", "verdict": "hype", "reason": "hallucinated id"},
        "garbage",
    ]})
    out = CannedAdapter(raw).classify_verdicts(items)
    assert out == {"verdicts": [{"id": "item_1", "verdict": "technical",
                                 "reason": "ok"}]}
    bad = CannedAdapter("not json at all").classify_verdicts(items)
    assert bad["error"] == "unparseable_model_output"
    assert CannedAdapter("").classify_verdicts([]) == {"error": "no_items"}


# -- MaterialPack truncation policy ----------------------------------------


def _rk(n: int, snippet: str = "s") -> dict:
    return {"id": f"item_{n}", "title": f"T{n}", "publisher": "P",
            "url": f"u{n}", "relevance": 1.0, "matched_terms": [],
            "published_at": None, "snippet": snippet}


def test_pack_filters_technical_and_keeps_rank_order():
    ranked = [_rk(1), _rk(2), _rk(3)]
    verdicts = {"item_1": {"verdict": "technical", "reason": "r1"},
                "item_2": {"verdict": "hype", "reason": "r2"},
                "item_3": {"verdict": "technical", "reason": "r3"}}
    pack = build_material_pack(ranked, verdicts)
    assert [i["id"] for i in pack["items"]] == ["item_1", "item_3"]
    assert pack["items"][0]["verdict"] == "technical"
    assert pack["items"][0]["verdict_reason"] == "r1"
    assert pack["stats"] == {"candidates": 3, "after_filter": 2, "in_pack": 2,
                             "dropped_over_budget": 0, "chars": pack["stats"]["chars"]}


def test_pack_without_verdicts_keeps_all_labeled():
    pack = build_material_pack([_rk(1), _rk(2)])
    assert pack["verdict_filter"] == "skipped"
    assert pack["stats"]["after_filter"] == 2


def test_pack_item_cap():
    pack = build_material_pack([_rk(n) for n in range(40)])
    assert pack["stats"]["in_pack"] == 30
    # the item cap cut 40 -> 30; the char budget cut nothing
    assert pack["stats"]["candidates"] - pack["stats"]["in_pack"] == 10
    assert pack["stats"]["dropped_over_budget"] == 0


def test_pack_char_budget_stops_at_first_overflow():
    ranked = [_rk(n, snippet="x" * 800) for n in range(5)]
    pack = build_material_pack(ranked, total_chars=2500)
    # each item is ~803 chars: three fit under 2500, the fourth overflows and
    # everything after it is dropped (rank-order prefix policy)
    assert pack["stats"]["in_pack"] == 3
    assert pack["stats"]["chars"] <= 2500
    assert pack["stats"]["dropped_over_budget"] == 2
