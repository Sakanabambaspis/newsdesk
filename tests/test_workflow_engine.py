"""The workflow engine contract, proven behavior-preserving (wayfinder
ticket 02).

The demonstration the contract demands: for equal database state, settings
and date, ``run_workflow(default-morning@1)`` and today's ``run_morning``
produce the same published artifacts (sidecar, audio, manifest, feed), the
same run report modulo ``finished_at``, and the same domain log entries in
order — the engine adds only its uniform stage events. Also pinned here:
the pre-flight ``selection`` tag, the idempotency guard, the dry-run
emission boundary, and the bounded repair loop (repairs → contained
degrade → loud). All offline: fixture feeds, a stubbed edge-tts save, the
local-dir publisher.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from newsdesk.config import Settings
from newsdesk.morning.orchestrator import run_morning
from newsdesk.morning.registries import SCRIPTWRITERS
from newsdesk.storage.db import Database
from newsdesk.storage.repo import ItemRepo, LogRepo, SourceRepo, WatchlistRepo
from newsdesk.workflow.engine import (WorkflowRunError, load_descriptor,
                                      run_workflow, validate_registration)
from tests.conftest import FEEDS, make_canonical_item

TOKEN = "3f9a1c77b2d84e05a1c3f6d2b9e08417"
DATE = "2026-09-20"
# MPEG2 Layer III, 24 kHz, 48 kbps: 576 samples/frame = 0.024 s
# (edge-tts format).
_FRAME = b"\xff\xf3\x64\x00" + b"\x00" * 140

# entries stages own themselves; the engine's uniform events are additive
DOMAIN_ACTIONS = ("daily_digest_built", "morning_brief_built",
                  "morning_audio_rendered", "morning_episode_published",
                  "morning_notify")


def _world(root: Path) -> tuple[Settings, Database]:
    home = root / "newsdesk-home"
    settings = Settings(
        home=home,
        db_url=f"sqlite:///{(home / 'newsdesk.db').as_posix()}",
        min_request_interval=0.0,
    )
    settings.feed_token = TOKEN
    settings.tts_pace = 0.0
    settings.ensure_dirs()
    return settings, Database(settings)


def _populate(session) -> None:
    source, _ = SourceRepo(session).add(
        (FEEDS / "sample-energy.xml").as_uri(), kind="rss")
    WatchlistRepo(session).create("dirs")
    WatchlistRepo(session).add_term(1, "agents")
    for n, rel in ((1, 0.9), (2, 0.6), (3, 0.5)):
        when = datetime.now(timezone.utc) - timedelta(hours=2 + n)
        item = make_canonical_item(
            url=f"https://x.example/{n}", title=f"Agents item {n}",
            text=f"Agents did a novel thing {n}. " * 6,
            id=f"item_morningtest{n:014d}",
            timestamps={"published_at": when.isoformat(),
                        "retrieved_at": when.isoformat()},
            analysis={"relevance": rel},
            provenance={"content_hash": f"h{n}"},
        )
        outcome, _row = ItemRepo(session).upsert(item, source_id=source.id)
        assert outcome == "created"


def _fake_edge_save(text, voice, path):
    path.write_bytes(_FRAME * (40 + len(text)))


def _entries(db: Database) -> list[tuple[str, dict[str, Any]]]:
    """The run's log as (action, detail) pairs, oldest first."""
    with db.session() as session:
        recent = LogRepo(session).recent(limit=100)
    return [(e.action, e.detail) for e in reversed(list(recent))]


def _normalize(value: Any, *homes: Path) -> Any:
    """Replace per-world home paths and fixture wall-clock timestamps so
    two worlds compare equal (item published_at are inputs, not behavior)."""
    if isinstance(value, str):
        for home in homes:
            value = value.replace(str(home), "<home>")
        return value
    if isinstance(value, dict):
        return {k: ("<ts>" if k == "published_at" and isinstance(v, str)
                    else _normalize(v, *homes))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v, *homes) for v in value]
    return value


def _run_both_chains(tmp_path: Path, monkeypatch
                     ) -> tuple[dict, dict, Any, Database, Database]:
    """Run the default chain twice on twin worlds: orchestrator, engine."""
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    settings_a, db_a = _world(tmp_path / "a")
    settings_b, db_b = _world(tmp_path / "b")
    with db_a.session() as session:
        _populate(session)
        report_a = run_morning(session, settings_a, date=DATE)
    with db_b.session() as session:
        _populate(session)
        report_b = run_workflow(session, settings_b,
                                load_descriptor("default-morning", 1),
                                date=DATE)
    return report_a, report_b, (settings_a, settings_b), db_a, db_b


def test_engine_reproduces_the_default_chain(tmp_path, monkeypatch):
    report_a, report_b, (settings_a, settings_b), db_a, db_b = \
        _run_both_chains(tmp_path, monkeypatch)
    homes = (settings_a.home, settings_b.home)

    assert set(report_a["stages"]) == set(report_b["stages"]) == {
        "collect", "digest", "script", "tts", "publish", "notify"}
    report_a = _normalize(dict(report_a), *homes)
    report_b = _normalize(dict(report_b), *homes)
    assert report_a.pop("finished_at") and report_b.pop("finished_at")
    assert report_a == report_b  # same report JSON, same stage payloads

    # the sidecar: same sections and stats (generated_at is wall-clock)
    sidecar_a = json.loads(
        (settings_a.morning_dir / DATE / f"{DATE}-script.json").read_text())
    sidecar_b = json.loads(
        (settings_b.morning_dir / DATE / f"{DATE}-script.json").read_text())
    sidecar_a.pop("generated_at"), sidecar_b.pop("generated_at")
    assert sidecar_a == sidecar_b

    # the audio: byte-identical for the same script
    mp3_a = (settings_a.publish_dir / TOKEN / "audio" / f"{DATE}.mp3"
             ).read_bytes()
    mp3_b = (settings_b.publish_dir / TOKEN / "audio" / f"{DATE}.mp3"
             ).read_bytes()
    assert mp3_a == mp3_b

    # the manifest: same episodes (published_at is wall-clock)
    manifest_a = json.loads(
        (settings_a.publish_dir / TOKEN / "episodes.json").read_text())
    manifest_b = json.loads(
        (settings_b.publish_dir / TOKEN / "episodes.json").read_text())
    for entry in manifest_a + manifest_b:
        entry.pop("published_at")
    assert manifest_a == manifest_b

    # the feed: same entries (pubDate is wall-clock, homes differ in URIs)
    feed_a = (settings_a.publish_dir / TOKEN / "feed.xml").read_text()
    feed_b = (settings_b.publish_dir / TOKEN / "feed.xml").read_text()
    feed_a = re.sub(r"<pubDate>[^<]*</pubDate>", "",
                    _normalize(feed_a, *homes))
    feed_b = re.sub(r"<pubDate>[^<]*</pubDate>", "",
                    _normalize(feed_b, *homes))
    assert feed_a == feed_b

    # the log: identical domain entries in order
    domain_a = [(a, _normalize(d, *homes)) for a, d in _entries(db_a)
                if a in DOMAIN_ACTIONS]
    domain_b = [(a, _normalize(d, *homes)) for a, d in _entries(db_b)
                if a in DOMAIN_ACTIONS]
    assert domain_a == domain_b

    # the run-terminal entries agree on every field they share
    finished_a = next(d for a, d in _entries(db_a)
                      if a == "morning_run_finished")
    finished_b = next(d for a, d in _entries(db_b)
                      if a == "workflow_run_finished")
    assert {k: finished_a[k] for k in ("date", "outcome")} == \
        {k: finished_b[k] for k in ("date", "outcome")}


def test_engine_emits_uniform_stage_events(tmp_path, monkeypatch):
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    settings, db = _world(tmp_path)
    with db.session() as session:
        _populate(session)
        run_workflow(session, settings,
                     load_descriptor("default-morning", 1), date=DATE)
    events = _entries(db)
    started = [d["stage"] for a, d in events if a == "workflow_stage_started"]
    assert started == ["collect", "digest", "script", "tts", "publish",
                       "notify"]
    seen = {d["stage"]: d for a, d in events
            if a == "workflow_stage_finished"}
    assert seen["digest"]["artifacts_in"] == ["collection"]
    assert seen["digest"]["artifacts_out"] == ["digest"]
    assert seen["script"]["artifacts_in"] == ["digest"]
    assert seen["tts"]["artifacts_out"] == ["audio"]
    assert seen["publish"]["artifacts_in"] == ["audio"]
    assert seen["notify"]["artifacts_out"] == []
    for detail in seen.values():
        assert detail["workflow"] == "default-morning"
        assert detail["version"] == 1
        assert detail["date"] == DATE and detail["attempt"] == 0
    finished = next(d for a, d in events if a == "workflow_run_finished")
    assert finished["outcome"] == "published"


def test_second_engine_run_short_circuits(tmp_path, monkeypatch):
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    settings, db = _world(tmp_path)
    with db.session() as session:
        _populate(session)
        run_workflow(session, settings,
                     load_descriptor("default-morning", 1), date=DATE)

    calls: list[int] = []

    def counting_save(text, voice, path):
        calls.append(1)
        path.write_bytes(_FRAME * 50)

    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", counting_save)
    with db.session() as session:
        report = run_workflow(session, settings,
                              load_descriptor("default-morning", 1),
                              date=DATE)
    assert report["outcome"] == "already_published"
    assert report["stages"] == {}
    assert calls == []  # the guard ran before any synthesis work
    finished = next(d for a, d in _entries(db)
                    if a == "workflow_run_finished" and
                    d["outcome"] == "already_published")
    assert finished["date"] == DATE


def test_dry_run_stops_before_the_emission_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    settings, db = _world(tmp_path)
    with db.session() as session:
        _populate(session)
        report = run_workflow(session, settings,
                              load_descriptor("default-morning", 1),
                              date=DATE, dry_run=True)
    assert report["outcome"] == "dry_run"
    assert set(report["stages"]) == {"collect", "digest", "script", "tts"}
    assert not (settings.publish_dir / TOKEN).exists()  # nothing published
    assert (settings.morning_dir / DATE / f"{DATE}.mp3").exists()  # audio did
    actions = [a for a, _ in _entries(db)]
    assert "morning_notify" not in actions and "morning_episode_published" \
        not in actions
    finished = next(d for a, d in _entries(db)
                    if a == "workflow_run_finished")
    assert finished["outcome"] == "dry_run"


def test_unknown_plugin_fails_preflight_tagged_selection(session, settings):
    settings.morning_tts = "nope"
    with pytest.raises(WorkflowRunError,
                       match=r"selection stage failed.*"
                             r"unknown TTS_ENGINES plugin 'nope'"):
        run_workflow(session, settings,
                     load_descriptor("default-morning", 1), date=DATE)
    events = [(e.action, e.detail)
              for e in reversed(list(LogRepo(session).recent(limit=100)))]
    failed = next(d for a, d in events if a == "workflow_run_failed")
    assert failed["stage"] == "selection" and "nope" in failed["error"]
    assert [a for a, _ in events if a == "workflow_stage_started"] == []


def test_unknown_pinned_plugin_fails_preflight(session, settings):
    doc = load_descriptor("default-morning", 1)
    doc["stages"][2]["plugin"] = "ghost"
    with pytest.raises(WorkflowRunError,
                       match=r"selection stage failed.*"
                             r"unknown SCRIPTWRITERS plugin 'ghost'"):
        run_workflow(session, settings, doc, date=DATE)


def test_mid_pipeline_failure_publishes_nothing(tmp_path, monkeypatch):
    settings, db = _world(tmp_path)
    settings.tts_retries = 0  # one attempt per voice: no real backoff sleeps

    def broken(text, voice, path):
        raise RuntimeError("endpoint gone")

    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", broken)
    with db.session() as session:
        _populate(session)
        with pytest.raises(WorkflowRunError,
                           match=r"tts stage failed: .*endpoint gone"):
            run_workflow(session, settings,
                         load_descriptor("default-morning", 1), date=DATE)
    assert not (settings.publish_dir / TOKEN).exists()  # no partial episode
    assert list(settings.morning_dir.glob("*/[0-9]*.mp3")) == []
    assert list(settings.morning_dir.glob("*/*.partial")) == []
    failed = next(d for a, d in _entries(db) if a == "workflow_run_failed")
    assert failed["stage"] == "tts"
    assert any(a == "workflow_stage_failed" for a, _ in _entries(db))


def _register_stub_compose(calls: list[int], pass_on_call: int) -> None:
    """A compose plugin that violates its word budget until ``pass_on_call``.

    Registered under a key nothing else selects; the engine reaches it only
    through the descriptor's ``plugin`` pin.
    """

    def stub_brief(settings, digest, *, date=None, **_):
        calls.append(1)
        words = 12 if len(calls) < pass_on_call else 2
        sections = [
            {"type": "headline", "text": " ".join(["w"] * words),
             "item_ids": [], "voice": "en-US-TestNeural", "est_seconds": 1},
            {"type": "close", "text": "That's the briefing.",
             "item_ids": [], "voice": "en-US-TestNeural", "est_seconds": 1},
        ]
        return {"date": date, "method": "stub",
                "stats": {"words": words, "est_seconds": 2},
                "sections": sections}

    SCRIPTWRITERS.register("stub-brief", stub_brief)


def _repair_descriptor(max_attempts: int | None = None) -> dict:
    doc: dict = {
        "format_version": 1, "name": "repair-demo", "version": 1,
        "stages": [
            {"type": "collect"}, {"type": "select"},
            {"type": "compose", "name": "script", "plugin": "stub-brief",
             "checks": [{"name": "word_budget",
                         "params": {"per_section": {"headline": 10}}}]},
            {"type": "render"}, {"type": "publish"}, {"type": "notify"},
        ],
    }
    if max_attempts is not None:
        doc["loop_policy"] = {"max_attempts": max_attempts}
    return doc


def test_repair_loop_lands_on_the_contained_degrade(tmp_path, monkeypatch):
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    calls: list[int] = []
    _register_stub_compose(calls, pass_on_call=4)  # only the degrade fits
    settings, db = _world(tmp_path)
    with db.session() as session:
        report = run_workflow(session, settings, _repair_descriptor(),
                              date=DATE)
    assert report["outcome"] == "published"
    assert len(calls) == 4  # initial + 2 repairs + 1 degrade (max_attempts 2)
    assert report["stages"]["script"]["method"] == "stub"
    sidecar = json.loads(
        (settings.morning_dir / DATE / f"{DATE}-script.json").read_text())
    assert sidecar["method"] == "stub"  # the degraded script is the real one


def test_repair_loop_is_bounded_then_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    settings, db = _world(tmp_path)

    calls: list[int] = []
    _register_stub_compose(calls, pass_on_call=99)  # never passes
    with db.session() as session:
        _populate(session)
        with pytest.raises(WorkflowRunError,
                           match=r"script stage failed:.*word_budget"):
            run_workflow(session, settings, _repair_descriptor(), date=DATE)
    assert len(calls) == 4  # bounded even when the check never passes
    failed = next(d for a, d in _entries(db) if a == "workflow_run_failed")
    assert failed["stage"] == "script"
    assert not (settings.publish_dir / TOKEN).exists()

    calls.clear()
    with db.session() as session:
        with pytest.raises(WorkflowRunError,
                           match=r"script stage failed:.*word_budget"):
            run_workflow(session, settings,
                         _repair_descriptor(max_attempts=1), date=DATE)
    assert len(calls) == 3  # initial + 1 repair + 1 degrade


def test_on_fail_is_per_check_not_per_stage(tmp_path, monkeypatch):
    """A passing ``fail`` sibling must not send a repairable violation
    loud: only a *violating* fail-check aborts (schema: on_fail is per
    check, ticket 01)."""
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    calls: list[int] = []
    _register_stub_compose(calls, pass_on_call=4)  # only the degrade fits
    doc = _repair_descriptor()
    doc["stages"][2]["checks"].insert(0, {
        "name": "section_allowlist", "on_fail": "fail",
        "params": {"allow": ["headline", "close"]}})  # the stub satisfies it
    settings, db = _world(tmp_path)
    with db.session() as session:
        report = run_workflow(session, settings, doc, date=DATE)
    assert report["outcome"] == "published"
    assert len(calls) == 4  # the word_budget repairs still ran


def test_registration_metadata_may_narrow_never_contradict():
    assert validate_registration("compose", ("digest",), ("script",)) == []
    assert validate_registration("collect", (), ("collection",)) == []
    assert validate_registration("compose", ("script",), ()) != []
    assert "does not" in validate_registration("compose", ("script",), ())[0]
    assert validate_registration("nowhere", (), ()) != []


def test_engine_rejects_invalid_descriptors_before_any_work(session,
                                                            settings):
    doc = load_descriptor("default-morning", 1)
    doc["format_version"] = 2
    with pytest.raises(Exception, match="format_version"):
        run_workflow(session, settings, doc, date=DATE)
