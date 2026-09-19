"""Rubric schema + scorer contract (wayfinder ticket 07).

A rubric is pure data — dimensions, weights, anchors, one threshold — that
conversational preference vocabulary compiles into; the scorer turns items
into per-dimension scores with stored, inspectable reasons (ADR 0001: the
model never places an item without one) and newsdesk-side weighted totals.
Proxied dimensions are computed mechanically in every path; the LLM scores
only the rest; any incomplete or unavailable model pass falls back to the
mechanical formula with the cause visible. These tests pin that contract
and the shipped draft rubric.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from newsdesk.llm.base import BaseLLMAdapter, LLMError, NullAdapter
from newsdesk.workflow.rubric import (RUBRIC_FORMAT_VERSION, RubricError,
                                      load_shipped_rubric, require_valid_rubric,
                                      score_items, story_key, validate_rubric)


NOW = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)


def base_rubric() -> dict:
    """One minimal valid rubric: one LLM-scored dimension, one proxied."""
    return {
        "format_version": RUBRIC_FORMAT_VERSION,
        "name": "test-taste",
        "version": 1,
        "title": "Test taste",
        "dimensions": [
            {"name": "depth", "description": "How deep it goes.",
             "weight": 0.7},
            {"name": "fit", "description": "Watchlist fit.",
             "weight": 0.3, "proxy": "relevance"},
        ],
    }


def card(cid: str, title: str, *, publisher: str = "Alpha Wire",
         relevance: float | None = 0.0, hours_ago: float = 1.0,
         matched: tuple[str, ...] = ("grid",), snippet: str = "body text") \
        -> dict:
    """One digest-shaped item card, published relative to NOW."""
    return {
        "id": cid, "title": title, "publisher": publisher,
        "relevance": relevance,
        "published_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "matched_terms": list(matched),
        "snippet": snippet,
    }


def demo_items() -> list[dict]:
    """The ticket-07 fixture set: deep vs hype vs a syndication triplet vs
    an old exclusive vs an off-window-topic item."""
    return [
        card("deep", "New grid-forming inverter topology passes field trial",
             relevance=0.5, hours_ago=1.0),
        card("hype", "GridCo raises $200M at a $2B valuation",
             relevance=1.0, hours_ago=2.0),
        card("syn-1", "Operator ships emergency load-shedding protocol",
             publisher="Alpha Wire", relevance=0.75, hours_ago=5.0),
        card("syn-2", "Operator ships emergency load-shedding protocol",
             publisher="Beta Herald", relevance=0.75, hours_ago=5.0),
        card("syn-3", "Operator ships emergency load-shedding protocol",
             publisher="Gamma Post", relevance=0.75, hours_ago=5.0),
        card("old", "Battery chemistry retrospective, one year on",
             relevance=1.0, hours_ago=23.9),
        card("off", "Celebrity opens a restaurant",
             relevance=None, hours_ago=1.0, matched=()),
    ]


def scores_by_id(result: dict) -> dict[str, dict]:
    return {s["id"]: s for s in result["scores"]}


# -- The shipped draft rubric --------------------------------------------------

def test_shipped_default_rubric_is_valid():
    rubric = load_shipped_rubric("default", 1)
    assert validate_rubric(rubric) == []
    assert rubric["name"] == "default" and rubric["version"] == 1
    names = [d["name"] for d in rubric["dimensions"]]
    assert names == ["technical_depth", "watchlist_fit", "timeliness",
                     "corroboration"]
    weights = {d["name"]: d["weight"] for d in rubric["dimensions"]}
    assert weights == {"technical_depth": 0.4, "watchlist_fit": 0.3,
                       "timeliness": 0.15, "corroboration": 0.15}
    proxies = {d["name"]: d.get("proxy") for d in rubric["dimensions"]}
    assert proxies == {"technical_depth": None, "watchlist_fit": "relevance",
                       "timeliness": "recency",
                       "corroboration": "corroboration"}
    # every dimension carries anchors — the inspectable standard the prompt
    # quotes verbatim
    assert all(d["anchors"] for d in rubric["dimensions"])
    assert rubric["thresholds"] == {"min_score": 0.0}


# -- Schema --------------------------------------------------------------------

def test_base_rubric_is_valid():
    assert validate_rubric(base_rubric()) == []


def test_format_version_gate():
    doc = base_rubric()
    del doc["format_version"]
    assert any("format_version" in e for e in validate_rubric(doc))
    doc = base_rubric() | {"format_version": 2}
    errors = validate_rubric(doc)
    assert any("format_version" in e and "1" in e for e in errors)


def test_name_and_version_rules():
    doc = base_rubric() | {"name": "Bad Name"}
    assert any("name" in e for e in validate_rubric(doc))
    doc = base_rubric() | {"version": 0}
    assert any("version" in e for e in validate_rubric(doc))
    doc = base_rubric() | {"version": "1"}
    assert any("version" in e for e in validate_rubric(doc))


def test_unknown_top_level_key_rejected():
    doc = base_rubric() | {"tone": "wry"}
    assert any("tone" in e for e in validate_rubric(doc))


def test_dimension_rules():
    doc = base_rubric()
    doc["dimensions"] = []
    assert any("dimensions" in e for e in validate_rubric(doc))

    doc = base_rubric()
    doc["dimensions"][0]["name"] = "Deep"
    assert any("name" in e for e in validate_rubric(doc))

    doc = base_rubric()
    doc["dimensions"][1]["name"] = "depth"
    errors = validate_rubric(doc)
    assert any("duplicate" in e and "depth" in e for e in errors)

    doc = base_rubric()
    del doc["dimensions"][0]["description"]
    assert any("description" in e for e in validate_rubric(doc))

    doc = base_rubric()
    doc["dimensions"][0]["description"] = "  "
    assert any("description" in e for e in validate_rubric(doc))

    for bad_weight in (0, -0.5, True, "0.4", None):
        doc = base_rubric()
        doc["dimensions"][0]["weight"] = bad_weight
        assert any("weight" in e for e in validate_rubric(doc)), bad_weight

    doc = base_rubric()
    doc["dimensions"][1]["proxy"] = "vibes"
    errors = validate_rubric(doc)
    assert any("proxy" in e and "vibes" in e for e in errors)

    doc = base_rubric()
    doc["dimensions"][0]["score_hint"] = "be nice"
    assert any("score_hint" in e for e in validate_rubric(doc))


def test_anchor_rules():
    def with_anchors(anchors: list) -> list[str]:
        doc = base_rubric()
        doc["dimensions"][0]["anchors"] = anchors
        return validate_rubric(doc)

    assert with_anchors([])  # present but empty is refused
    assert with_anchors([{"score": 1.2, "example": "too high"}])
    assert with_anchors([{"score": 0.5, "example": ""}])
    assert with_anchors([{"score": 0.5}])  # no example
    assert with_anchors([{"score": 0.5, "example": "ok", "note": "x"}])
    assert with_anchors([{"score": 0.5, "example": "a"},
                         {"score": 0.5, "example": "b"}])  # duplicate level
    assert with_anchors([{"score": 0.0, "example": "low"},
                         {"score": 1.0, "example": "high"}]) == []


def test_threshold_rules():
    doc = base_rubric() | {"thresholds": {"min_score": 1.5}}
    assert any("min_score" in e for e in validate_rubric(doc))
    doc = base_rubric() | {"thresholds": {"floor": 0.2}}
    assert any("floor" in e for e in validate_rubric(doc))
    doc = base_rubric() | {"thresholds": {"min_score": 0.5}}
    assert validate_rubric(doc) == []


def test_require_valid_rubric_lists_all_violations():
    doc = base_rubric() | {"format_version": 2, "extra": 1}
    doc["dimensions"][0]["weight"] = -1
    with pytest.raises(RubricError) as excinfo:
        require_valid_rubric(doc)
    message = str(excinfo.value)
    for fragment in ("format_version", "extra", "weight"):
        assert fragment in message


# -- Mechanical signals ---------------------------------------------------------

def test_story_key_normalization():
    assert story_key("EV Grid MEGA-deal!!!  announced") \
        == "ev grid mega deal announced"
    assert story_key(None) == ""
    assert story_key("  ") == ""


def test_mechanical_formula_pins():
    """The no-key formula on the default rubric: weights renormalize over
    the three proxied dimensions (0.3/0.15/0.15 of 0.6) →
    total = 0.5*relevance + 0.25*recency + 0.25*corroboration."""
    rubric = load_shipped_rubric("default", 1)
    result = score_items(rubric, demo_items(), NullAdapter(),
                         window_hours=24, now=NOW)
    assert result["method"] == "mechanical"
    assert result["rubric"] == "default@1"
    assert result["llm_error"]  # the fallback cause stays visible
    assert result["unassessed"] == ["technical_depth"]
    got = scores_by_id(result)
    assert got["syn-1"]["total"] == pytest.approx(0.8229, abs=1e-3)
    assert got["hype"]["total"] == pytest.approx(0.8125, abs=1e-3)
    assert got["old"]["total"] == pytest.approx(0.5844, abs=1e-3)
    assert got["deep"]["total"] == pytest.approx(0.5729, abs=1e-3)
    assert got["off"]["total"] == pytest.approx(0.3229, abs=1e-3)
    mechanical_order = [s["id"] for s in
                        sorted(result["scores"], key=lambda s: -s["total"])]
    assert mechanical_order == ["syn-1", "syn-2", "syn-3", "hype", "old",
                                "deep", "off"]


def test_mechanical_reasons_are_inspectable():
    rubric = load_shipped_rubric("default", 1)
    got = scores_by_id(score_items(rubric, demo_items(), NullAdapter(),
                                   window_hours=24, now=NOW))
    deep = got["deep"]["dimensions"]
    assert "0.50" in deep["watchlist_fit"]["reason"]
    assert "1.0h" in deep["timeliness"]["reason"] and "24h" \
        in deep["timeliness"]["reason"]
    assert deep["corroboration"]["reason"] == "carried by 1 distinct publisher"
    assert "no watchlist terms configured" \
        in got["off"]["dimensions"]["watchlist_fit"]["reason"]


def test_mechanical_missing_timestamp_scores_zero_with_reason():
    rubric = load_shipped_rubric("default", 1)
    item = card("notime", "Undated press release", relevance=1.0)
    item["published_at"] = None
    got = scores_by_id(score_items(rubric, [item], NullAdapter(),
                                   window_hours=24, now=NOW))
    dims = got["notime"]["dimensions"]
    assert dims["timeliness"]["score"] == 0.0
    assert "no publish timestamp" in dims["timeliness"]["reason"]


def test_malformed_signal_values_degrade_instead_of_crashing():
    """The fallback must not raise on degenerate per-item data — a value
    it cannot read scores 0.0 with a visible reason, like a missing one."""
    rubric = load_shipped_rubric("default", 1)
    bad_rel = card("badrel", "Unreadable relevance", relevance="high",
                   hours_ago=1.0)
    bad_ts = card("badts", "Unparseable timestamp", relevance=1.0)
    bad_ts["published_at"] = "the day before yesterday"
    got = scores_by_id(score_items(rubric, [bad_rel, bad_ts], NullAdapter(),
                                   window_hours=24, now=NOW))
    assert got["badrel"]["dimensions"]["watchlist_fit"]["score"] == 0.0
    assert "unreadable relevance" \
        in got["badrel"]["dimensions"]["watchlist_fit"]["reason"]
    assert got["badts"]["dimensions"]["timeliness"]["score"] == 0.0
    assert "unreadable publish timestamp" \
        in got["badts"]["dimensions"]["timeliness"]["reason"]


def test_corroboration_counts_distinct_publishers():
    rubric = load_shipped_rubric("default", 1)
    wire = "Operator ships emergency protocol"
    same_outlet_twice = [card("a", wire, publisher="Alpha"),
                         card("b", wire, publisher="Alpha")]
    got = scores_by_id(score_items(rubric, same_outlet_twice, NullAdapter(),
                                   window_hours=24, now=NOW))
    assert got["a"]["dimensions"]["corroboration"]["score"] \
        == pytest.approx(1 / 3, abs=1e-3)

    three_outlets = [card("a", wire, publisher="Alpha"),
                     card("b", wire, publisher="Beta"),
                     card("c", wire, publisher="Gamma"),
                     card("d", wire, publisher="Delta")]
    got = scores_by_id(score_items(rubric, three_outlets, NullAdapter(),
                                   window_hours=24, now=NOW))
    for cid in ("a", "b", "c", "d"):
        dims = got[cid]["dimensions"]
        assert dims["corroboration"]["score"] == 1.0
        assert "4 distinct publishers" in dims["corroboration"]["reason"]


# -- The LLM path ----------------------------------------------------------------

class StubScorer:
    """Scripted stand-in with the adapter's score_rubric shape."""

    name = "stub"

    def __init__(self, scores: dict):
        self.scores = scores
        self.asked_rubric: list[str] = []

    def score_rubric(self, rubric: dict, items: list[dict]) -> dict:
        self.asked_rubric.append(rubric["name"])
        return {"scores": [{"id": cid, "dimensions": dict(dims)}
                           for cid, dims in self.scores.items()
                           if cid in {i["id"] for i in items}]}


def test_score_items_llm_path_asks_only_unproxied_dimensions():
    rubric = load_shipped_rubric("default", 1)
    depth = {"deep": 0.9, "hype": 0.1, "syn-1": 0.7, "syn-2": 0.7,
             "syn-3": 0.7, "old": 0.8, "off": 0.0}
    stub = StubScorer({cid: {"technical_depth": {"score": s,
                                                 "reason": "grounded clause"}}
                       for cid, s in depth.items()})
    result = score_items(rubric, demo_items(), stub,
                         window_hours=24, now=NOW)
    assert stub.asked_rubric == ["default"]
    assert result["method"] == "llm:stub"
    assert "llm_error" not in result and "unassessed" not in result
    got = scores_by_id(result)
    # total = 0.4*0.9 + 0.3*0.5 + 0.15*(23/24) + 0.15*(1/3)
    assert got["deep"]["total"] == pytest.approx(0.7038, abs=1e-3)
    # the model's stored reason rides through; computed dimensions carry
    # mechanical reasons in this path too
    assert got["deep"]["dimensions"]["technical_depth"]["reason"] \
        == "grounded clause"
    assert "0.50" in got["deep"]["dimensions"]["watchlist_fit"]["reason"]


def test_score_items_falls_back_when_no_llm_configured():
    rubric = load_shipped_rubric("default", 1)
    result = score_items(rubric, demo_items(), NullAdapter(),
                         window_hours=24, now=NOW)
    assert result["method"] == "mechanical"
    assert "llm_not_configured" in result["llm_error"]
    assert result["unassessed"] == ["technical_depth"]


def test_score_items_falls_back_on_llm_error():
    class Broken:
        name = "broken"

        def score_rubric(self, rubric, items):
            raise LLMError("upstream 500")

    rubric = load_shipped_rubric("default", 1)
    result = score_items(rubric, demo_items(), Broken(),
                         window_hours=24, now=NOW)
    assert result["method"] == "mechanical"
    assert "upstream 500" in result["llm_error"]


def test_score_items_is_all_or_nothing():
    """A partial model pass is never mixed with mechanical fills — scores
    must stay comparable across items; the whole pass falls back."""
    rubric = load_shipped_rubric("default", 1)
    stub = StubScorer({"deep": {"technical_depth":
                                {"score": 0.9, "reason": "measured"}}})
    result = score_items(rubric, demo_items(), stub,
                         window_hours=24, now=NOW)
    assert result["method"] == "mechanical"
    assert "did not score" in result["llm_error"]
    assert result["unassessed"] == ["technical_depth"]


def test_score_items_rejects_out_of_range_scores():
    rubric = load_shipped_rubric("default", 1)
    stub = StubScorer({i["id"]: {"technical_depth":
                                 {"score": 1.5, "reason": "overconfident"}}
                       for i in demo_items()})
    result = score_items(rubric, demo_items(), stub,
                         window_hours=24, now=NOW)
    assert result["method"] == "mechanical"
    assert "did not score" in result["llm_error"]


def test_all_proxied_rubric_needs_no_llm():
    doc = {
        "format_version": RUBRIC_FORMAT_VERSION, "name": "pure-mech",
        "version": 1,
        "dimensions": [
            {"name": "fit", "description": "Watchlist fit.", "weight": 1.0,
             "proxy": "relevance"},
        ],
    }
    item = card("a", "T", relevance=0.8)
    result = score_items(doc, [item], NullAdapter(),
                         window_hours=24, now=NOW)
    assert result["method"] == "mechanical"
    assert "llm_error" not in result and "unassessed" not in result
    assert result["scores"][0]["total"] == pytest.approx(0.8, abs=1e-3)


def test_empty_items_are_skipped():
    rubric = load_shipped_rubric("default", 1)
    result = score_items(rubric, [], NullAdapter(), window_hours=24, now=NOW)
    assert result == {"rubric": "default@1", "method": "skipped:no_items",
                      "scores": []}


def test_score_items_refuses_invalid_rubric():
    doc = base_rubric()
    doc["dimensions"][0]["weight"] = 0
    with pytest.raises(RubricError):
        score_items(doc, demo_items(), NullAdapter(), now=NOW)


def test_rubric_preferences_reorder_the_pack():
    """The ticket-07 demo, pinned: the same fixture set ranked by the
    mechanical formula vs scored against the draft rubric's taste. The
    rubric's technical_depth dimension is exactly what moves 'deep' above
    'hype' — the mechanical formula cannot see the difference."""
    rubric = load_shipped_rubric("default", 1)
    items = demo_items()

    mechanical = score_items(rubric, items, NullAdapter(),
                             window_hours=24, now=NOW)
    mechanical_order = [s["id"] for s
                        in sorted(mechanical["scores"],
                                  key=lambda s: -s["total"])]

    depth = {"deep": (0.9, "novel topology with measured field results"),
             "hype": (0.1, "funding round, no technical core"),
             "syn-1": (0.7, "real operational protocol"),
             "syn-2": (0.7, "real operational protocol"),
             "syn-3": (0.7, "real operational protocol"),
             "old": (0.8, "substantive retrospective"),
             "off": (0.0, "no engineering content")}
    stub = StubScorer({cid: {"technical_depth":
                             {"score": s, "reason": r}}
                       for cid, (s, r) in depth.items()})
    scored = score_items(rubric, items, stub, window_hours=24, now=NOW)
    scored_order = [s["id"] for s
                    in sorted(scored["scores"], key=lambda s: -s["total"])]

    assert mechanical_order.index("hype") < mechanical_order.index("deep")
    assert scored_order.index("deep") < scored_order.index("hype")
    # the stored reasons are the audit trail (ADR 0001)
    top = scores_by_id(scored)[scored_order[0]]
    assert top["dimensions"]["technical_depth"]["reason"]


# -- The adapter contract (llm/base.py) ------------------------------------------

class ScriptedLLM(BaseLLMAdapter):
    """complete() returns canned text; everything else is the real base."""

    name = "scripted"

    def __init__(self, raw: str):
        self.raw = raw

    def complete(self, system, user, *, max_tokens=1200, temperature=0.2):
        return self.raw


def test_base_score_rubric_happy_path():
    raw = json.dumps({"scores": [
        {"id": "a", "dimensions": {"depth": {"score": 0.75,
                                             "reason": " novel mechanism "}}},
        {"id": "ghost", "dimensions": {"depth": {"score": 1.0,
                                                 "reason": "not provided"}}},
        {"id": "a", "dimensions": {"mystery": {"score": 1.0,
                                               "reason": "unknown dim"}}},
    ]})
    result = ScriptedLLM(raw).score_rubric(base_rubric(),
                                           [card("a", "T", relevance=0.5)])
    assert result["scores"] == [
        {"id": "a", "dimensions": {"depth": {"score": 0.75,
                                             "reason": "novel mechanism"}}}]


def test_base_score_rubric_unparseable():
    result = ScriptedLLM("not json at all").score_rubric(
        base_rubric(), [card("a", "T", relevance=0.5)])
    assert result["error"] == "unparseable_model_output"


def test_base_score_rubric_no_items():
    result = ScriptedLLM("{}").score_rubric(base_rubric(), [])
    assert result["error"] == "no_items"


def test_null_adapter_score_rubric_reports_unconfigured():
    result = NullAdapter().score_rubric(base_rubric(),
                                        [card("a", "T", relevance=0.5)])
    assert result["error"] == "llm_not_configured"
