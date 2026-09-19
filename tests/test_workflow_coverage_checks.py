"""The 2026-09-19 repro loops, now check-guarded (wayfinder ticket 05).

The episode postmortem that founded the workflow architecture diagnosed two
failure modes of the hardcoded chain: a syndication cluster eating the
episode's slots (``pack[0:4]`` covered one story four times) and
single-theme coverage (the episode never left one watchlist term). Neither
was guarded — the episode published whatever the top slots held.

These loops rebuild both worlds offline and assert the new behavior: the
``default-morning@1`` descriptor's coverage checks (``distinct_stories``,
``diversity_floor``) fail the run loudly — bounded repairs, then the
contained degrade, then a stage-tagged failure and nothing published —
instead of silently shipping a collapsed episode. The scaling rule (a
floor can never demand more than the pack offered) is pinned too: healthy
mixed worlds and quiet days still publish.

Fully offline: file:// fixture feed for the collect stage, keyless LLM
(the extractive path), a stubbed edge-tts save, the local-dir publisher.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from newsdesk.morning.registries import SCRIPTWRITERS
from newsdesk.storage.repo import (ItemRepo, LogRepo, SourceRepo,
                                   WatchlistRepo)
from newsdesk.workflow.engine import (WorkflowRunError, load_descriptor,
                                      run_workflow)
from tests.conftest import FEEDS, make_canonical_item

TOKEN = "3f9a1c77b2d84e05a1c3f6d2b9e08417"
DATE = "2026-09-20"
# MPEG2 Layer III, 24 kHz, 48 kbps: 576 samples/frame = 0.024 s
# (edge-tts format).
_FRAME = b"\xff\xf3\x64\x00" + b"\x00" * 140


def _fake_edge_save(text, voice, path):
    path.write_bytes(_FRAME * (40 + len(text)))


def _mk_item(session, source, n: int, *, title: str, text: str,
             relevance: float) -> None:
    when = datetime.now(timezone.utc) - timedelta(hours=1 + n / 10)
    item = make_canonical_item(
        url=f"https://x.example/{n}", title=title, text=text,
        id=f"item_covertest{n:014d}",
        timestamps={"published_at": when.isoformat(),
                    "retrieved_at": when.isoformat()},
        analysis={"relevance": relevance},
        provenance={"content_hash": f"h{n}"},
    )
    outcome, _row = ItemRepo(session).upsert(item, source_id=source.id)
    assert outcome == "created"  # distinct hashes: syndication survives dedup


def _watchlist(session, terms: tuple[str, ...]) -> None:
    WatchlistRepo(session).create("dirs")
    for term in terms:
        WatchlistRepo(session).add_term(1, term)


def _syndicated_world(session) -> None:
    """Loop 1's world: four wire copies of one story hold the pack's top
    slots (each its own item: distinct url/hash, shared wire headline),
    with distinct stories ranked below — the 2026-09-19 episode's pack."""
    source, _ = SourceRepo(session).add(
        (FEEDS / "sample-energy.xml").as_uri(), kind="rss")
    _watchlist(session, ("agents",))
    for n, rel in ((1, 0.9), (2, 0.85), (3, 0.8), (4, 0.75)):
        _mk_item(session, source, n, title="Agent framework ships memory "
                                           "upgrade",
                 text=f"Outlets confirm the agents story {n}. " * 4,
                 relevance=rel)
    _mk_item(session, source, 5, title="Agents learn to plan ahead",
             text="Agents did a novel thing five. " * 4, relevance=0.4)
    _mk_item(session, source, 6, title="Agents browse with tools",
             text="Agents did a novel thing six. " * 4, relevance=0.3)


def _single_theme_world(session) -> None:
    """Loop 2's world: distinct stories on top, but all four episode slots
    in one theme while a second theme sits ranked below — the episode
    never leaves 'agents'."""
    source, _ = SourceRepo(session).add(
        (FEEDS / "sample-energy.xml").as_uri(), kind="rss")
    _watchlist(session, ("agents", "reasoning"))
    for n, rel in ((1, 0.9), (2, 0.85), (3, 0.8), (4, 0.75)):
        _mk_item(session, source, n, title=f"Agent story number {n}",
                 text="Agents did a novel thing. " * 4, relevance=rel)
    _mk_item(session, source, 5, title="Reasoning benchmark scales up",
             text="Reasoning did a novel thing five. " * 4, relevance=0.4)
    _mk_item(session, source, 6, title="Reasoning distillation works",
             text="Reasoning did a novel thing six. " * 4, relevance=0.3)


def _mixed_world(session) -> None:
    """A healthy two-theme world: the floors scale to what the pack
    offered, so normal coverage publishes."""
    source, _ = SourceRepo(session).add(
        (FEEDS / "sample-energy.xml").as_uri(), kind="rss")
    _watchlist(session, ("agents", "reasoning"))
    for n, rel in ((1, 0.9), (2, 0.6)):
        _mk_item(session, source, n, title=f"Agent story number {n}",
                 text="Agents did a novel thing. " * 4, relevance=rel)
    _mk_item(session, source, 3, title="Reasoning benchmark scales up",
             text="Reasoning did a novel thing three. " * 4, relevance=0.5)


def _entries(session) -> list[tuple[str, dict[str, Any]]]:
    """The run's log as (action, detail) pairs, oldest first."""
    recent = LogRepo(session).recent(limit=200)
    return [(e.action, e.detail) for e in reversed(list(recent))]


def _run_failed(session) -> dict[str, Any]:
    return next(d for a, d in _entries(session) if a == "workflow_run_failed")


@pytest.fixture
def offline(settings, monkeypatch):
    settings.feed_token = TOKEN
    settings.tts_pace = 0.0  # no inter-chunk sleeps on the stubbed synth
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    return settings


def _run(session, settings, doc=None):
    return run_workflow(session, settings,
                        doc or load_descriptor("default-morning", 1),
                        date=DATE)


# -- loop 1: syndication-cluster collapse ---------------------------------------


def test_cluster_collapse_fails_loud_instead_of_publishing_a_one_story_episode(
        session, offline):
    _syndicated_world(session)
    with pytest.raises(WorkflowRunError, match=r"script stage failed: "
                                               r".*distinct_stories"):
        _run(session, offline)
    assert not (offline.publish_dir / TOKEN).exists()  # nothing published
    failed = _run_failed(session)
    assert failed["stage"] == "script"
    assert "the pack offers 3" in failed["error"]  # other stories existed


def test_cluster_collapse_runs_the_bounded_repairs_first(session, offline):
    """1 + 2 repairs + 1 contained degrade: four compose invocations, each
    logged, before the loud failure (loop_policy.max_attempts 2)."""
    _syndicated_world(session)
    with pytest.raises(WorkflowRunError):
        _run(session, offline)
    briefs = [d for a, d in _entries(session) if a == "morning_brief_built"]
    assert len(briefs) == 4


# -- loop 2: cross-theme diversity floor ----------------------------------------


def test_single_theme_coverage_fails_the_diversity_floor(session, offline):
    _single_theme_world(session)
    with pytest.raises(WorkflowRunError, match=r"script stage failed: "
                                               r".*diversity_floor"):
        _run(session, offline)
    assert not (offline.publish_dir / TOKEN).exists()
    failed = _run_failed(session)
    assert failed["stage"] == "script"
    # the distinct-stories sibling passed (3 stories covered): the floor
    # that fired is the theme floor, isolating this loop from loop 1
    assert "distinct_stories" not in failed["error"]


# -- the floors scale: healthy and quiet worlds still publish --------------------


def test_mixed_theme_world_still_publishes(session, offline):
    _mixed_world(session)
    report = _run(session, offline)
    assert report["outcome"] == "published"
    script = json.loads(
        (offline.morning_dir / DATE / f"{DATE}-script.json").read_text())
    assert [s["type"] for s in script["sections"]] == [
        "cold_open", "headline", "headline", "deep_dive", "close"]


def test_quiet_day_publishes_a_short_episode(session, offline):
    """An empty pack scales both floors to zero: a quiet day is legal."""
    source, _ = SourceRepo(session).add(
        (FEEDS / "sample-energy.xml").as_uri(), kind="rss")
    _watchlist(session, ("agents",))
    report = _run(session, offline)
    assert report["outcome"] == "published"
    script = json.loads(
        (offline.morning_dir / DATE / f"{DATE}-script.json").read_text())
    assert [s["type"] for s in script["sections"]] == ["cold_open", "close"]


# -- the degrade pass is contained: no LLM call ----------------------------------


class _CountingAdapter:
    name = "counting"

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, *, max_tokens=1200, temperature=0.2):
        self.calls += 1
        return "not json at all"


def test_degrade_pass_skips_the_llm_call(session, offline, monkeypatch):
    """The contained degrade must be contained: the context-native writer
    produces its extractive fill without re-calling the model."""
    adapter = _CountingAdapter()
    monkeypatch.setattr("newsdesk.morning.scriptwriter.get_adapter",
                        lambda _settings: adapter)
    source, _ = SourceRepo(session).add(
        (FEEDS / "sample-energy.xml").as_uri(), kind="rss")
    _watchlist(session, ("agents",))
    _mk_item(session, source, 1, title="Agent story number one",
             text="Agents did a novel thing. " * 30, relevance=0.9)
    doc = load_descriptor("default-morning", 1)
    doc["stages"][2]["checks"] = [
        {"name": "word_budget", "params": {"total": 1}}]  # never satisfiable
    with pytest.raises(WorkflowRunError, match=r"script stage failed"):
        _run(session, offline, doc)
    assert adapter.calls == 3  # initial + 2 repairs; the degrade skipped it


# -- duration_band: the sixth check, unattached but implemented -------------------


def test_duration_band_guards_the_rendered_episode(session, offline):
    _mixed_world(session)
    doc = load_descriptor("default-morning", 1)
    doc["stages"][3]["checks"] = [
        {"name": "duration_band",
         "params": {"min_seconds": 0, "max_seconds": 20}}]
    with pytest.raises(WorkflowRunError,
                       match=r"tts stage failed: .*band maximum"):
        _run(session, offline, doc)
    assert not (offline.publish_dir / TOKEN).exists()


# -- registration metadata (wired at ticket 05) -----------------------------------


def test_registration_contradicting_triple_fails_at_registration():
    with pytest.raises(ValueError,
                       match=r"contradicts stage type 'compose'"):
        SCRIPTWRITERS.register("narrow-bad", lambda: None, stage="compose",
                               requires=("script",))
    SCRIPTWRITERS.register("narrow-ok", lambda: None, stage="compose",
                           requires=(), provides=("script",))  # narrows: fine
    assert not SCRIPTWRITERS.is_context_native("narrow-ok")


def test_registration_unknown_stage_type_fails():
    with pytest.raises(ValueError, match="unknown stage type 'nowhere'"):
        SCRIPTWRITERS.register("nowhere-plugin", lambda: None,
                               stage="nowhere")


def test_built_ins_declare_their_stages():
    from newsdesk.morning.registries import load_plugins

    load_plugins()  # idempotent: registers the built-ins with metadata
    assert SCRIPTWRITERS.is_context_native("llm-brief")


def test_unknown_stage_param_fails_preflight_tagged_selection(session,
                                                              settings):
    """Registration closed the plugins' params key sets: a param no plugin
    declared fails pre-flight, having done no work."""
    from newsdesk.storage.repo import SourceRepo

    SourceRepo(session).add((FEEDS / "sample-energy.xml").as_uri(),
                            kind="rss")
    doc = load_descriptor("default-morning", 1)
    doc["stages"][3]["params"] = {"voice": "en-GB-RyanNeural"}
    with pytest.raises(WorkflowRunError,
                       match=r"selection stage failed: .*unknown params"):
        _run(session, settings, doc)
    started = [d for a, d in _entries(session)
               if a == "workflow_stage_started"]
    assert started == []  # nothing ran


def test_select_hours_param_is_a_declared_key(session, offline):
    """The select built-in closes {"hours"}: the @1 descriptor's params
    are declared, not accidentally accepted."""
    source, _ = SourceRepo(session).add(
        (FEEDS / "sample-energy.xml").as_uri(), kind="rss")
    _watchlist(session, ("agents",))
    _mk_item(session, source, 1, title="Agent story number one",
             text="Agents did a novel thing. " * 4, relevance=0.9)
    report = _run(session, offline)
    assert report["outcome"] == "published"
