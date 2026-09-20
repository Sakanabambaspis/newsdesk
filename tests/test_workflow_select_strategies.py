"""W3: the select strategies, cluster collapse, and the repair bound
(wayfinder tickets 08 + 09).

The three registered strategy plugins run the common pipeline — window →
30-cap → verdict filter → rubric scoring (mechanical fallback offline) →
``min_score`` admission → pick → pack — and the engine dispatches to them
only when a select stage pins ``plugin``; the unpinned select stays the
legacy built-in, so frozen ``default-morning@1`` keeps its pinned
behavior. The 2026-09-19 regression shape is closed intrinsically here:
syndication clusters collapse at pick time, so copies can no longer fill
episode slots — and the coverage floors measure the *admissible set*, so
a small pick cannot lower its own floor. Strategies never enter the
repair loop; the checks stay post-compose as the defect backstop.

Fully offline: file:// fixture feed, keyless LLM (mechanical scoring, the
extractive writer), a stubbed edge-tts save, the local-dir publisher.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from newsdesk.storage.repo import (ItemRepo, LogRepo, SourceRepo,
                                   WatchlistRepo)
from newsdesk.workflow.engine import (WorkflowRunError, load_descriptor,
                                      run_workflow)
from newsdesk.workflow.strategies import (_pick_mix, _pick_single,
                                          _pick_top_k)
from newsdesk.workflow.rubric_catalog import (RubricCatalog,
                                              ensure_default_rubric_catalog)
from tests.conftest import FEEDS, make_canonical_item

TOKEN = "3f9a1c77b2d84e05a1c3f6d2b9e08417"
DATE = "2026-09-20"
_FRAME = b"\xff\xf3\x64\x00" + b"\x00" * 140  # one edge-tts MP3 frame


def _fake_edge_save(text, voice, path):
    path.write_bytes(_FRAME * (40 + len(text)))


def _mk_item(session, source, n: int, *, title: str, text: str,
             relevance: float, publisher: str = "Alpha Wire") -> str:
    when = datetime.now(timezone.utc) - timedelta(hours=1 + n / 10)
    item = make_canonical_item(
        url=f"https://{publisher.lower().replace(' ', '')}.example/{n}",
        title=title, text=text, id=f"item_seltest{n:014d}",
        source={"publisher": publisher, "url": f"https://x.example/{n}",
                "kind": "article", "author": None},
        timestamps={"published_at": when.isoformat(),
                    "retrieved_at": when.isoformat()},
        analysis={"relevance": relevance},
        provenance={"content_hash": f"h{n}"},
    )
    outcome, row = ItemRepo(session).upsert(item, source_id=source.id)
    assert outcome == "created"  # distinct hashes: syndication survives dedup
    return row.id


def _watchlist(session, terms: tuple[str, ...]) -> None:
    WatchlistRepo(session).create("dirs")
    for term in terms:
        WatchlistRepo(session).add_term(1, term)


def _world(session, terms: tuple[str, ...]) -> Any:
    source, _ = SourceRepo(session).add(
        (FEEDS / "sample-energy.xml").as_uri(), kind="rss")
    _watchlist(session, terms)
    return source


def _mixed_world(session) -> dict[str, str]:
    """Three distinct stories, two themes, one outlet each; ranked by
    stored relevance."""
    source = _world(session, ("agents", "reasoning"))
    return {
        "deep": _mk_item(session, source, 1,
                         title="Agent framework ships memory upgrade",
                         text="Agents did a novel thing one. " * 4,
                         relevance=0.9),
        "mid": _mk_item(session, source, 2,
                        title="Agents learn to plan ahead",
                        text="Agents did a novel thing two. " * 4,
                        relevance=0.6),
        "third": _mk_item(session, source, 3,
                          title="Reasoning benchmark scales up",
                          text="Reasoning did a novel thing three. " * 4,
                          relevance=0.5),
    }


def _syndicated_world(session) -> dict[str, str]:
    """The 2026-09-19 shape: three wire copies of one story hold the top
    relevance slots; two other stories rank below."""
    source = _world(session, ("agents",))
    ids: dict[str, str] = {}
    for n, (publisher, rel) in enumerate(
            [("Alpha Wire", 0.9), ("Beta Herald", 0.85),
             ("Gamma Post", 0.8)], start=1):
        ids[f"syn{n}"] = _mk_item(
            session, source, n, title="Agent framework ships memory upgrade",
            text=f"Outlets confirm the agents story {n}. " * 4,
            relevance=rel, publisher=publisher)
    ids["other1"] = _mk_item(session, source, 4,
                             title="Agents learn to plan ahead",
                             text="Agents did a novel thing four. " * 4,
                             relevance=0.4, publisher="Alpha Wire")
    ids["other2"] = _mk_item(session, source, 5,
                             title="Agents browse with tools",
                             text="Agents did a novel thing five. " * 4,
                             relevance=0.3, publisher="Alpha Wire")
    return ids


def _trend_world(session) -> dict[str, str]:
    """One substantive exclusive (best total), a 3-outlet cluster, a
    2-outlet cluster, and a filler story — for the mix construction."""
    source = _world(session, ("agents", "grid"))
    ids = {
        "deep": _mk_item(session, source, 1,
                         title="Inverter topology passes field trial",
                         text="Grid did a novel thing one. " * 4,
                         relevance=0.9, publisher="Alpha Wire"),
        "t1a": _mk_item(session, source, 2,
                        title="Sensor grid goes mainstream",
                        text="Agents did a novel thing two. " * 4,
                        relevance=0.5, publisher="Beta Herald"),
        "t1b": _mk_item(session, source, 3,
                        title="Sensor grid goes mainstream",
                        text="Agents did a novel thing three. " * 4,
                        relevance=0.5, publisher="Gamma Post"),
        "t1c": _mk_item(session, source, 4,
                        title="Sensor grid goes mainstream",
                        text="Agents did a novel thing four. " * 4,
                        relevance=0.5, publisher="Delta Tribune"),
        "t2a": _mk_item(session, source, 5,
                        title="Battery swap network expands",
                        text="Agents did a novel thing five. " * 4,
                        relevance=0.4, publisher="Beta Herald"),
        "t2b": _mk_item(session, source, 6,
                        title="Battery swap network expands",
                        text="Agents did a novel thing six. " * 4,
                        relevance=0.4, publisher="Epsilon Union"),
        "fill": _mk_item(session, source, 7,
                         title="Microgrid controller released",
                         text="Grid did a novel thing seven. " * 4,
                         relevance=0.45, publisher="Alpha Wire"),
    }
    return ids


# -- the pick functions, pure (the construction's edge cases) ---------------------


def _story(key: str, total: float, outlets: int,
           published: str = "") -> dict[str, Any]:
    return {"key": key, "total": total, "published": published,
            "rep": {"id": key, "outlets": outlets}}


def test_pick_top_k_orders_by_total_then_k():
    stories = [_story("a", 0.9, 1), _story("c", 0.5, 3), _story("b", 0.7, 1)]
    assert _pick_top_k(stories, 3) == [{"id": "a", "outlets": 1},
                                       {"id": "b", "outlets": 1},
                                       {"id": "c", "outlets": 3}]
    assert _pick_top_k(stories, 2) == [{"id": "a", "outlets": 1},
                                       {"id": "b", "outlets": 1}]


def test_pick_top_k_tie_breaks_on_the_representative_id():
    """The spec's total order: total, then fresher, then id — at story
    level the id is the representative's."""
    stories = [_story("z", 0.5, 1, published="2026-09-20T01:00:00"),
               _story("y", 0.5, 1, published="2026-09-20T01:00:00")]
    for s in stories:
        s["rep"]["id"] = s["key"]
    assert [r["id"] for r in _pick_top_k(stories, 1)] == ["y"]


def test_pick_mix_leads_with_impact_then_trending_slots():
    stories = [_story("deep", 0.9, 1), _story("t3", 0.5, 3),
               _story("t2", 0.4, 2), _story("fill", 0.3, 1)]
    # pack[0] = best impactful; then floor(4/2)=2 trending slots by
    # outlets; then impactful fills the rest
    assert _pick_mix(stories, 4) == [
        {"id": "deep", "outlets": 1}, {"id": "t3", "outlets": 3},
        {"id": "t2", "outlets": 2}, {"id": "fill", "outlets": 1}]
    assert _pick_mix(stories, 3) == [
        {"id": "deep", "outlets": 1}, {"id": "t3", "outlets": 3},
        {"id": "t2", "outlets": 2}]
    assert _pick_mix(stories, 1) == [{"id": "deep", "outlets": 1}]


def test_pick_mix_deduplicates_across_pools_and_never_pads():
    # the top story is also the trendiest: the trending slots skip it
    stories = [_story("big", 0.9, 3), _story("mid", 0.5, 1)]
    assert _pick_mix(stories, 9) == [{"id": "big", "outlets": 3},
                                     {"id": "mid", "outlets": 1}]
    assert _pick_mix([], 4) == []


def test_pick_single_takes_one_story_or_none():
    stories = [_story("a", 0.9, 1), _story("b", 0.8, 3)]
    assert _pick_single(stories) == [{"id": "a", "outlets": 1}]
    assert _pick_single([]) == []


# -- strategy runs through the engine -----------------------------------------------

@pytest.fixture
def offline(settings, monkeypatch):
    settings.feed_token = TOKEN
    settings.tts_pace = 0.0  # no inter-chunk sleeps on the stubbed synth
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    return settings


_COVER_CHECKS = [{"name": "distinct_stories", "params": {"min_distinct": 2}},
                 {"name": "diversity_floor", "params": {"min_themes": 2}}]


def _descriptor(strategy: str, params: dict[str, Any], *,
                checks: list[dict[str, Any]] | None = _COVER_CHECKS,
                full_chain: bool = True) -> dict[str, Any]:
    """A strategy workflow: select pins the strategy plugin; the rubric
    is the shipped default (floated) unless params say otherwise."""
    stages: list[dict[str, Any]] = [
        {"type": "collect"},
        {"type": "select", "name": "digest", "plugin": strategy,
         "params": {"rubric": "default", **params}},
    ]
    if full_chain:
        stages += [
            {"type": "compose", "name": "script", "params": {},
             "checks": checks or []},
            {"type": "render", "name": "tts", "params": {}},
            {"type": "publish", "params": {},
             "checks": [{"name": "archive_intact", "on_fail": "fail"}]},
            {"type": "notify", "params": {}},
        ]
    return {"format_version": 1, "name": "strategy-demo", "version": 1,
            "stages": stages}


def _entries(session) -> list[tuple[str, dict[str, Any]]]:
    recent = LogRepo(session).recent(limit=200)
    return [(e.action, e.detail) for e in reversed(list(recent))]


def _select_scored(session) -> dict[str, Any]:
    return next(d for a, d in _entries(session) if a == "select_scored")


# -- the 2026-09-19 regression: a cluster can no longer fill the slots --------


def test_three_syndicated_copies_cannot_fill_three_episode_slots(
        session, offline):
    ids = _syndicated_world(session)
    report = run_workflow(session, offline,
                          _descriptor("top-k-interesting", {"k": 3}),
                          date=DATE)
    assert report["outcome"] == "published"
    script = json.loads(
        (offline.morning_dir / DATE / f"{DATE}-script.json").read_text())
    spoken = [iid for s in script["sections"] for iid in s["item_ids"]]
    assert len(spoken) == len(set(spoken))  # no item spoke twice
    syndicated = {ids["syn1"], ids["syn2"], ids["syn3"]}
    assert len(set(spoken) & syndicated) == 1  # exactly one copy survives
    scored = _select_scored(session)
    assert scored["in_pack"] == 3  # the cluster collapsed to its rep
    assert {"outlets": 3} in [{k: v for k, v in card.items()
                               if k == "outlets"}
                              for card in scored["items"]]


def test_select_report_exposes_pack_stats_and_scoring(session, offline):
    ids = _mixed_world(session)
    doc = _descriptor("top-k-interesting", {"k": 2},
                      checks=[{"name": "distinct_stories",
                               "params": {"min_distinct": 2}},
                              {"name": "diversity_floor",
                               "params": {"min_themes": 1}}])
    report = run_workflow(session, offline, doc, date=DATE, dry_run=True)
    select_report = report["stages"]["digest"]
    assert select_report["method"] == "top-k-interesting"
    assert select_report["rubric"] == "default@1"
    assert select_report["scoring_method"] == "mechanical"  # keyless run
    assert "llm_error" in _select_scored(session)  # the cause stays visible
    # the shrinkage triple: window → admitted → packed
    assert select_report["candidates"] == 3
    assert select_report["after_filter"] == 3
    assert select_report["in_pack"] == 2
    # ticket 07: the stored reasons ride the report too
    assert {s["id"] for s in select_report["scores"]} == \
        set(ids.values())


def test_strategies_are_selectable_in_a_dry_run(session, offline):
    """Acceptance: all three strategies runnable via descriptor param and
    selectable in a dry-run — a minimal collect+select chain per
    strategy."""
    _mixed_world(session)
    for strategy, params in (("top-k-interesting", {"k": 2}),
                             ("trending-impactful-mix", {"k": 2}),
                             ("single-deep-dive", {})):
        report = run_workflow(session, offline,
                              _descriptor(strategy, params,
                                          full_chain=False),
                              date=DATE, dry_run=True)
        assert report["outcome"] == "dry_run"
        assert report["stages"]["digest"]["method"] == strategy


def test_full_chain_dry_run_stops_before_the_emission_boundary(
        session, offline):
    _mixed_world(session)
    report = run_workflow(session, offline,
                          _descriptor("top-k-interesting", {"k": 3}),
                          date=DATE, dry_run=True)
    assert set(report["stages"]) == {"collect", "digest", "script", "tts"}
    assert not (offline.publish_dir / TOKEN).exists()


# -- the strategies pick what their policies say --------------------------------


def test_top_k_packs_the_k_best_stories_in_total_order(session, offline):
    ids = _mixed_world(session)
    run_workflow(session, offline,
                 _descriptor("top-k-interesting", {"k": 3}), date=DATE)
    scored = _select_scored(session)
    assert [i["id"] for i in scored["items"]] == \
        [ids["deep"], ids["mid"], ids["third"]]  # relevance-desc totals
    assert all("outlets" not in i or i["outlets"] == 1
               for i in scored["items"])


def test_trending_impactful_mix_builds_the_ticket08_pack(session, offline):
    """pack[0] = best impactful (the substance), then trending slots by
    corroboration breadth — not a relevance list with the same order."""
    ids = _trend_world(session)
    run_workflow(session, offline,
                 _descriptor("trending-impactful-mix", {"k": 4}), date=DATE)
    scored = _select_scored(session)
    assert [i["id"] for i in scored["items"]] == [
        ids["deep"],  # best impactful: the deep dive is substance
        ids["t1a"],   # trending: 3 outlets (its cluster's freshest copy)
        ids["t2a"],   # trending: 2 outlets
        ids["fill"],  # impactful fills the rest
    ]
    outlets = {i["id"]: i["outlets"] for i in scored["items"]}
    assert outlets == {ids["deep"]: 1, ids["t1a"]: 3, ids["t2a"]: 2,
                       ids["fill"]: 1}


def test_single_deep_dive_packs_one_story_or_none(session, offline):
    ids = _mixed_world(session)
    report = run_workflow(session, offline,
                          _descriptor("single-deep-dive", {},
                                      checks=[{"name": "distinct_stories",
                                               "params": {"min_distinct": 1}},
                                              {"name": "diversity_floor",
                                               "params": {"min_themes": 1}}]),
                          date=DATE)
    assert report["outcome"] == "published"
    scored = _select_scored(session)
    assert [i["id"] for i in scored["items"]] == [ids["deep"]]
    script = json.loads(
        (offline.morning_dir / DATE / f"{DATE}-script.json").read_text())
    assert [s["type"] for s in script["sections"]] == [
        "cold_open", "deep_dive", "close"]  # never padded


def test_single_deep_dive_on_a_quiet_day_publishes_short(session, offline):
    _world(session, ("agents",))  # a source, no items
    report = run_workflow(session, offline,
                          _descriptor("single-deep-dive", {}), date=DATE)
    assert report["outcome"] == "published"
    assert _select_scored(session)["in_pack"] == 0
    script = json.loads(
        (offline.morning_dir / DATE / f"{DATE}-script.json").read_text())
    assert [s["type"] for s in script["sections"]] == ["cold_open", "close"]


# -- admission: min_score shrinks the pack, never pads ---------------------------


def test_min_score_admission_shrinks_the_admissible_set(session, offline):
    _mixed_world(session)
    ensure_default_rubric_catalog(session)
    catalog = RubricCatalog(session)
    strict = deepcopy(catalog.resolve("default"))  # the doc is a private copy
    strict["name"] = "strict"
    strict["version"] = 1
    strict["title"] = "Only strong stories"
    strict["thresholds"] = {"min_score": 0.9}  # nothing mechanical passes
    catalog.create_version(strict, actor="user")
    doc = _descriptor("top-k-interesting", {"k": 3, "rubric": "strict"})
    report = run_workflow(session, offline, doc, date=DATE)
    assert report["outcome"] == "published"  # packs shrink; a quiet day is legal
    select_report = report["stages"]["digest"]
    assert select_report["candidates"] == 3
    assert select_report["after_filter"] == 0  # nothing cleared the bar
    assert select_report["in_pack"] == 0
    assert select_report["rubric"] == "strict@1"
    script = json.loads(
        (offline.morning_dir / DATE / f"{DATE}-script.json").read_text())
    assert [s["type"] for s in script["sections"]] == ["cold_open", "close"]


# -- the floors measure the admissible set: a pick cannot lower its own floor ----


def test_single_theme_pick_cannot_lower_its_own_floor(session, offline):
    """The diversity floor scales to the *admissible* set (two themes
    offered), not the pack: a single-deep-dive pack of one theme now
    fails — under the old pack-based base it would have passed by
    lowering its own floor to 1."""
    _mixed_world(session)
    doc = _descriptor("single-deep-dive", {})  # keeps the default 2-floors
    with pytest.raises(WorkflowRunError,
                       match=r"script stage failed: .*diversity_floor"):
        run_workflow(session, offline, doc, date=DATE)
    assert not (offline.publish_dir / TOKEN).exists()
    failed = next(d for a, d in _entries(session)
                  if a == "workflow_run_failed")
    assert "the admissible set offers 2" in failed["error"]


def test_strategy_run_repairs_bounded_then_fails_loud(session, offline):
    """Acceptance: the repair loop stays bounded on a strategy workflow —
    a persistent floor violation runs initial + 2 repairs + 1 contained
    degrade (4 compose invocations), then aborts with nothing published.
    Strategies themselves never enter the loop."""
    _mixed_world(session)
    doc = _descriptor("single-deep-dive", {})
    with pytest.raises(WorkflowRunError, match=r"script stage failed"):
        run_workflow(session, offline, doc, date=DATE)
    briefs = [d for a, d in _entries(session) if a == "morning_brief_built"]
    assert len(briefs) == 4  # loop_policy.max_attempts=2: 1 + 2 + 1
    started = [d for a, d in _entries(session)
               if a == "workflow_stage_started" and d["stage"] == "digest"]
    assert len(started) == 1  # the strategy ran exactly once


# -- the rubric ref resolves at pre-flight ----------------------------------------


def _selection_error(session, settings, doc):
    with pytest.raises(WorkflowRunError,
                       match=r"selection stage failed") as excinfo:
        run_workflow(session, settings, doc, date=DATE)
    started = [d for a, d in _entries(session)
               if a == "workflow_stage_started"]
    assert started == []  # loud, having done no work
    return str(excinfo.value)


def test_missing_rubric_param_fails_preflight(session, offline):
    doc = _descriptor("top-k-interesting", {"k": 2})
    del doc["stages"][1]["params"]["rubric"]
    error = _selection_error(session, offline, doc)
    assert "requires a 'rubric' param" in error


def test_unknown_rubric_ref_fails_preflight(session, offline):
    doc = _descriptor("top-k-interesting", {"k": 2, "rubric": "ghost"})
    assert "unknown rubric 'ghost'" in _selection_error(session, offline, doc)


def test_retired_rubric_ref_fails_preflight(session, offline):
    ensure_default_rubric_catalog(session)
    RubricCatalog(session).retire("default", actor="user")
    doc = _descriptor("top-k-interesting", {"k": 2})
    assert "is retired" in _selection_error(session, offline, doc)


def test_malformed_rubric_ref_fails_preflight(session, offline):
    doc = _descriptor("top-k-interesting", {"k": 2, "rubric": "default@x"})
    assert "malformed rubric ref" in _selection_error(session, offline, doc)


def test_rubric_ref_floats_at_latest_and_pins_at_version(session, offline):
    _mixed_world(session)
    ensure_default_rubric_catalog(session)
    catalog = RubricCatalog(session)
    v2 = catalog.resolve("default")
    v2["version"] = 2
    v2["title"] = "A new taste"
    catalog.create_version(v2, actor="user")

    floated = run_workflow(session, offline,
                           _descriptor("top-k-interesting", {"k": 3}),
                           date=DATE, dry_run=True)
    assert floated["stages"]["digest"]["rubric"] == "default@2"

    pinned = run_workflow(session, offline,
                          _descriptor("top-k-interesting",
                                      {"k": 3, "rubric": "default@1"}),
                          date=DATE, dry_run=True)
    assert pinned["stages"]["digest"]["rubric"] == "default@1"


# -- pre-flight: impossible check bindings and multi-pin collapses ---------------


def test_check_bound_before_its_artifacts_fails_preflight(session, offline):
    """Ticket 08: the binding guard 'refuses loudly' — at pre-flight, so
    a select stage can never run a doomed repair loop on a check whose
    artifacts only exist later."""
    _mixed_world(session)
    doc = _descriptor("top-k-interesting", {"k": 3})
    doc["stages"][1]["checks"] = [dict(_COVER_CHECKS[0])]  # coverage at select
    error = _selection_error(session, offline, doc)
    assert "can never pass must not enter the repair loop" in error
    assert "'script'" in error
    doc2 = _descriptor("top-k-interesting", {"k": 3})
    doc2["stages"][2]["checks"] = [
        {"name": "duration_band",
         "params": {"min_seconds": 0, "max_seconds": 10}}]  # audio at compose
    assert "at or after a 'render' stage" in _selection_error(session,
                                                              offline, doc2)


def test_two_select_stages_pinning_different_strategies_fail_preflight(
        session, offline):
    """One strategy resolves per select stage type: two different pins
    are a silent-collapse risk, refused loudly having done no work."""
    _mixed_world(session)
    doc = _descriptor("top-k-interesting", {"k": 3})
    doc["stages"].insert(2, {
        "type": "select", "name": "digest-2",
        "plugin": "single-deep-dive", "params": {"rubric": "default"}})
    error = _selection_error(session, offline, doc)
    assert "multiple select stages pin different strategies" in error


def test_outlets_count_the_whole_scored_cluster(session, offline):
    """``outlets`` is the corroboration number: distinct publishers across
    the scored candidate set — a copy evicted by ``min_score`` still
    counts as an outlet carrying the story."""
    source = _world(session, ("agents",))
    for n, (publisher, rel) in enumerate(
            [("Alpha Wire", 0.9), ("Beta Herald", 0.5),
             ("Gamma Post", 0.5)], start=1):
        _mk_item(session, source, n, title="Agent framework ships upgrade",
                 text=f"Outlets confirm the agents story {n}. " * 4,
                 relevance=rel, publisher=publisher)
    ensure_default_rubric_catalog(session)
    catalog = RubricCatalog(session)
    strict = deepcopy(catalog.resolve("default"))
    strict["name"] = "bar"
    strict["version"] = 1
    strict["thresholds"] = {"min_score": 0.8}  # admits only the 0.9 copy
    catalog.create_version(strict, actor="user")
    run_workflow(session, offline,
                 _descriptor("top-k-interesting",
                             {"k": 1, "rubric": "bar@1"}),
                 date=DATE, dry_run=True)
    scored = _select_scored(session)
    assert scored["after_filter"] == 1  # one copy cleared the bar
    assert scored["items"][0]["outlets"] == 3  # three carried the story


# -- params and pins ---------------------------------------------------------------


def test_bad_strategy_params_fail_the_select_stage(session, offline):
    _mixed_world(session)
    with pytest.raises(WorkflowRunError,
                       match=r"digest stage failed:.*integer k >= 1"):
        run_workflow(session, offline,
                     _descriptor("top-k-interesting", {"k": 0}), date=DATE)
    with pytest.raises(WorkflowRunError,
                       match=r"digest stage failed:.*hours"):
        run_workflow(session, offline,
                     _descriptor("top-k-interesting",
                                 {"k": 2, "hours": "late"}), date=DATE)
    assert not (offline.publish_dir / TOKEN).exists()


def test_unpinned_select_stays_the_legacy_builtin(session, offline):
    """The frozen-descriptor rule: without a ``plugin`` pin the select
    stage is the legacy digest — no strategy, no select_scored entry."""
    _mixed_world(session)
    doc = load_descriptor("default-morning", 1)
    report = run_workflow(session, offline, doc, date=DATE, dry_run=True)
    assert report["stages"]["digest"]["method"] == "extractive"
    assert not any(a == "select_scored" for a, _ in _entries(session))
