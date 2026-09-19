"""Characterization harness for the default morning chain (wayfinder 03).

Pins the observable outputs of today's hardcoded chain —
``orchestrator.run_morning`` and its stage functions — so the W1
workflow-module rewrite is provably behavior-preserving, per the repo's
characterization-first methodology (docs/reviews/fixture-findings-20260917.md).
Every pinned value was observed, not invented: if a stage's behavior changes
its test goes red, and the pin is either restored (bug) or flipped
deliberately (intended change) — never silently.

Coverage, per the ticket: digest shape for a fixture window; script sidecar
sections/stats on the extractive and stubbed-LLM paths; audio report shape
for a stubbed synth; publish report + manifest for local-dir; and the
full-run report plus the ordered log-entry sequence.

Fully offline by construction: the fixture feed is collected over file://,
the digest/script stages run keyless (NullAdapter — the LLM path is stubbed
at the adapter seam), TTS runs on synthetic MP3 frames behind the ``engine``
seam, and publishing uses local-dir. No network, no LLM key.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from newsdesk.morning.edgetts import edge_tts_synth
from newsdesk.morning.orchestrator import run_morning, stage_script
from newsdesk.morning.publish import publish_local
from newsdesk.morning.registries import load_plugins
from newsdesk.pipeline.digest import build_daily_digest
from newsdesk.storage.repo import ItemRepo, LogRepo, SourceRepo, WatchlistRepo
from tests.conftest import FEEDS, make_canonical_item

TOKEN = "3f9a1c77b2d84e05a1c3f6d2b9e08417"
DATE = "2026-09-20"
# MPEG2 Layer III, 24 kHz, 48 kbps: 576 samples/frame = 0.024 s per frame
# (edge-tts format); one frame is 144 bytes.
_FRAME = b"\xff\xf3\x64\x00" + b"\x00" * 140


def _fake_edge_save(text, voice, path):
    path.write_bytes(_FRAME * (40 + len(text)))


def _mk_item(session, source, n: int, *, title: str, text: str,
             age_hours: float, relevance: float | None = None) -> str:
    when = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    item = make_canonical_item(
        url=f"https://x.example/{n}", title=title, text=text,
        id=f"item_chaintest{n:014d}",
        timestamps={"published_at": when.isoformat(),
                    "retrieved_at": when.isoformat()},
        analysis={"relevance": relevance},
        provenance={"content_hash": f"h{n}"},
    )
    outcome, row = ItemRepo(session).upsert(item, source_id=source.id)
    assert outcome == "created"
    return row.id


@pytest.fixture
def chain_world(session):
    """The fixture window: one local feed (out of window by pubDate), a
    watchlist term, and three fresh items ranked by stored relevance.
    Returns the three item ids, ranked first by stored relevance."""
    load_plugins()  # idempotent: stage-level tests bypass run_morning's call
    source, _ = SourceRepo(session).add((FEEDS / "sample-energy.xml").as_uri(),
                                        kind="rss")
    WatchlistRepo(session).create("dirs")
    WatchlistRepo(session).add_term(1, "agents")
    return {
        f"i{n}": _mk_item(session, source, n, title=f"Agents item {n}",
                          text=f"Agents did a novel thing {n}. " * 6,
                          age_hours=2 + n, relevance=rel)
        for n, rel in ((1, 0.9), (2, 0.6), (3, 0.5))
    }


@pytest.fixture
def offline_chain(settings, chain_world, monkeypatch):
    """The full chain offline: fake synth seam, local-dir, empty notifiers."""
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    settings.feed_token = TOKEN
    settings.tts_pace = 0.0
    return settings


def _entries(session) -> list[tuple[str, dict[str, Any]]]:
    """The run's log as (action, detail) pairs, oldest first."""
    recent = LogRepo(session).recent(limit=100)
    return [(e.action, e.detail) for e in reversed(list(recent))]


def _normalize(value: Any, home: Path) -> Any:
    """Replace the per-test home path so pins hold in any tmp dir."""
    if isinstance(value, str):
        return value.replace(str(home), "<home>")
    if isinstance(value, dict):
        return {k: _normalize(v, home) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v, home) for v in value]
    return value


# -- digest stage ----------------------------------------------------------------


def test_digest_shape_for_a_fixture_window(session, settings, chain_world):
    ids = chain_world
    digest = build_daily_digest(session, settings, hours=24)

    assert set(digest) == {"overview", "worth_following", "also_noteworthy",
                           "noise", "method", "verdict_method",
                           "material_pack", "generated_at", "window_hours",
                           "items_in_window", "items_considered", "items"}
    assert digest["method"] == "extractive"
    assert digest["verdict_method"] == "skipped:llm_not_configured"
    assert digest["window_hours"] == 24
    assert digest["items_in_window"] == 3 and digest["items_considered"] == 3
    assert digest["overview"] == (
        "3 items in the window, top 3 considered; 1 watchlist themes matched. "
        "This briefing is extractive (no LLM configured) — set "
        "NEWSDESK_LLM_BASE_URL and NEWSDESK_LLM_API_KEY for an analyst "
        "narrative.")
    assert digest["worth_following"] == [{
        "theme": "agents (3 items)",
        "why": "Grouped by watchlist term; no LLM configured, so no narrative.",
        "item_ids": [ids["i1"], ids["i2"], ids["i3"]],
    }]
    assert digest["also_noteworthy"] == []
    assert digest["noise"] == \
        "Items not matching any watchlist term were skipped from themes."

    # item cards, ranked by relevance; no verdicts on the keyless path
    assert [c["title"] for c in digest["items"]] == \
        ["Agents item 1", "Agents item 2", "Agents item 3"]
    for card in digest["items"]:
        assert set(card) == {"id", "title", "publisher", "url", "relevance",
                             "matched_terms", "published_at", "verdict",
                             "verdict_reason"}
        assert card["verdict"] is None and card["verdict_reason"] is None
        assert card["matched_terms"] == ["agents"]

    # the writer's pack: mechanical-v1, verdict filter skipped, stats
    # flattened into the dict (not nested)
    pack = digest["material_pack"]
    assert set(pack) == {"method", "verdict_filter", "verdict_counts",
                         "candidates", "after_filter", "in_pack",
                         "dropped_over_budget", "chars", "items"}
    assert pack["method"] == "mechanical-v1"
    assert pack["verdict_filter"] == "skipped"
    assert pack["verdict_counts"] == {"technical": 0, "hype": 0, "tangential": 0}
    assert pack["candidates"] == 3 and pack["after_filter"] == 3
    assert pack["in_pack"] == 3 and pack["dropped_over_budget"] == 0
    assert pack["chars"] == 543  # 3 items x (13-char title + \n + 167-char snippet)
    assert pack["items"][0]["text"] == (
        "Agents item 1\n"
        "Agents did a novel thing 1. Agents did a novel thing 1. "
        "Agents did a novel thing 1. Agents did a novel thing 1. "
        "Agents did a novel thing 1. Agents did a novel thing 1.")
    for pack_item in pack["items"]:
        assert set(pack_item) == {"id", "title", "publisher", "url",
                                  "relevance", "matched_terms",
                                  "published_at", "verdict", "verdict_reason",
                                  "text"}

    # the digest stage's log entry: ADR 0001 posture on the keyless path
    built = [d for a, d in _entries(session) if a == "daily_digest_built"]
    assert len(built) == 1
    assert built[0]["method"] == "extractive"
    assert built[0]["verdict_method"] == "skipped:llm_not_configured"
    assert built[0]["items_in_window"] == 3 and built[0]["items_considered"] == 3
    assert built[0]["verdicts"] == []
    assert built[0]["verdict_counts"] == {"technical": 0, "hype": 0,
                                          "tangential": 0}


# -- script stage ----------------------------------------------------------------


def test_script_sidecar_extractive_path(session, settings, chain_world):
    ids = chain_world
    digest = build_daily_digest(session, settings, hours=24)

    brief = stage_script(session, settings, digest, DATE)
    assert brief["method"] == "extractive"
    assert brief["date"] == DATE
    assert [(s["type"], s["item_ids"]) for s in brief["sections"]] == [
        ("cold_open", []),
        ("headline", [ids["i2"]]),
        ("headline", [ids["i3"]]),
        ("deep_dive", [ids["i1"]]),
        ("close", []),
    ]
    assert brief["stats"] == {"words": 141, "est_seconds": 57}

    sidecar = json.loads(
        (settings.morning_dir / DATE / f"{DATE}-script.json").read_text())
    assert set(sidecar) == {"date", "generated_at", "method", "stats",
                            "sections"}
    sidecar.pop("generated_at")  # wall clock
    assert sidecar == {
        "date": DATE,
        "method": "extractive",
        "stats": {"words": 141, "est_seconds": 57},
        "sections": [
            {"type": "cold_open", "text": "Your morning briefing is ready.",
             "item_ids": [], "voice": "en-US-AriaNeural", "est_seconds": 2},
            {"type": "headline",
             "text": "Agents item 2. Agents item 2 Agents did a novel thing "
                     "2. Agents did a novel thing 2. Agents did a novel "
                     "thing 2. Agents did a novel thing 2. Agents did a "
                     "novel thing 2. Agents did a novel thing 2.",
             "item_ids": [ids["i2"]], "voice": "en-US-AriaNeural",
             "est_seconds": 17},
            {"type": "headline",
             "text": "Agents item 3. Agents item 3 Agents did a novel thing "
                     "3. Agents did a novel thing 3. Agents did a novel "
                     "thing 3. Agents did a novel thing 3. Agents did a "
                     "novel thing 3. Agents did a novel thing 3.",
             "item_ids": [ids["i3"]], "voice": "en-US-AriaNeural",
             "est_seconds": 17},
            {"type": "deep_dive",
             "text": "Agents item 1. Agents item 1 Agents did a novel thing "
                     "1. Agents did a novel thing 1. Agents did a novel "
                     "thing 1. Agents did a novel thing 1. Agents did a "
                     "novel thing 1. Agents did a novel thing 1.",
             "item_ids": [ids["i1"]], "voice": "en-US-AriaNeural",
             "est_seconds": 17},
            {"type": "close",
             "text": "That's the briefing — the full digest has the details.",
             "item_ids": [], "voice": "en-US-AriaNeural", "est_seconds": 4},
        ],
    }

    built = [d for a, d in _entries(session) if a == "morning_brief_built"]
    assert len(built) == 1
    assert built[0]["date"] == DATE and built[0]["method"] == "extractive"
    assert built[0]["stats"] == {"words": 141, "est_seconds": 57}
    assert [(s["type"], s["item_ids"], s["est_seconds"])
            for s in built[0]["sections"]] == [
        ("cold_open", [], 2),
        ("headline", [ids["i2"]], 17),
        ("headline", [ids["i3"]], 17),
        ("deep_dive", [ids["i1"]], 17),
        ("close", [], 4),
    ]
    assert built[0]["sidecar"] == str(
        settings.morning_dir / DATE / f"{DATE}-script.json")


class _StubScriptAdapter:
    """Writer-side LLM stub: complete() replays a fixed model reply."""

    name = "stub"

    def __init__(self, reply: str):
        self.reply = reply

    def complete(self, system, user, *, max_tokens=1200, temperature=0.2):
        return self.reply


def test_script_sidecar_llm_path_with_stub_adapter(session, settings,
                                                   chain_world, monkeypatch):
    ids = chain_world
    deep_text = " ".join(["The mechanism held up under load."] * 40)
    reply = json.dumps({
        "headlines": [
            {"id": ids["i2"],
             "text": "Watchlist item two moved markets today because the "
                     "evidence changed."},
            {"id": ids["i3"],
             "text": "Item three closed the loop on the open question from "
                     "last week."},
        ],
        "deep_dive": {"id": ids["i1"], "text": deep_text},
    })
    monkeypatch.setattr("newsdesk.morning.scriptwriter.get_adapter",
                        lambda _settings: _StubScriptAdapter(reply))

    digest = build_daily_digest(session, settings, hours=24)
    brief = stage_script(session, settings, digest, DATE)

    assert brief["method"] == "llm:stub"
    assert [(s["type"], s["item_ids"]) for s in brief["sections"]] == [
        ("cold_open", []),
        ("headline", [ids["i2"]]),
        ("headline", [ids["i3"]]),
        ("deep_dive", [ids["i1"]]),
        ("close", []),
    ]
    # the model's prose is spoken verbatim (under budget), framed by the
    # deterministic cold open and close
    assert [s["text"] for s in brief["sections"]] == [
        "Your morning briefing is ready.",
        "Watchlist item two moved markets today because the evidence changed.",
        "Item three closed the loop on the open question from last week.",
        deep_text,
        "That's the briefing — the full digest has the details.",
    ]
    assert brief["stats"] == {"words": 277, "est_seconds": 111}

    sidecar = json.loads(
        (settings.morning_dir / DATE / f"{DATE}-script.json").read_text())
    assert sidecar["method"] == "llm:stub"
    assert sidecar["stats"] == {"words": 277, "est_seconds": 111}
    for section in sidecar["sections"]:
        assert set(section) == {"type", "text", "item_ids", "voice",
                                "est_seconds"}
        assert section["voice"] == "en-US-AriaNeural"


# -- tts stage -------------------------------------------------------------------


def test_audio_report_shape_for_a_stubbed_synth(settings):
    script_dir = settings.morning_dir / DATE
    script_dir.mkdir(parents=True)
    script_path = script_dir / f"{DATE}-script.json"
    script_path.write_text(json.dumps({
        "date": DATE,
        "sections": [
            {"type": "cold_open", "text": "Your morning briefing is ready.",
             "item_ids": [], "voice": "en-US-AriaNeural", "est_seconds": 2},
            {"type": "headline",
             "text": "First story happened today. It matters because "
                     "evidence moved.",
             "item_ids": ["item_chaintest00000000000009"],
             "voice": "en-US-AriaNeural", "est_seconds": 4},
            {"type": "deep_dive", "text": "One. Two. Three. Four. Five.",
             "item_ids": ["item_chaintest00000000000010"],
             "voice": "en-US-AriaNeural", "est_seconds": 2},
        ],
    }))

    report = edge_tts_synth(settings, script_path, engine=_fake_edge_save,
                            sleep=lambda _s: None)

    # 5 chunks (1 + 1 + 3), 319 frames total -> 7.7 s measured, not estimated
    assert _normalize(report, settings.home) == {
        "date": DATE, "engine": "edge-tts",
        "mp3": f"<home>/morning/{DATE}/{DATE}.mp3",
        "duration_seconds": 7.7, "chunks": 5,
        "voices": ["en-US-AriaNeural"], "sample_rate": 24000,
        "audio_json": f"<home>/morning/{DATE}/{DATE}-audio.json",
    }
    assert (settings.morning_dir / DATE / f"{DATE}.mp3").stat().st_size \
        == 144 * 319

    audio_sidecar = json.loads(
        (settings.morning_dir / DATE / f"{DATE}-audio.json").read_text())
    assert audio_sidecar["manifest"] == [
        {"section_index": 0, "section_type": "cold_open", "chunk_index": 0,
         "voice": "en-US-AriaNeural", "words": 5, "failures": []},
        {"section_index": 1, "section_type": "headline", "chunk_index": 0,
         "voice": "en-US-AriaNeural", "words": 9, "failures": []},
        {"section_index": 2, "section_type": "deep_dive", "chunk_index": 0,
         "voice": "en-US-AriaNeural", "words": 2, "failures": []},
        {"section_index": 2, "section_type": "deep_dive", "chunk_index": 1,
         "voice": "en-US-AriaNeural", "words": 2, "failures": []},
        {"section_index": 2, "section_type": "deep_dive", "chunk_index": 2,
         "voice": "en-US-AriaNeural", "words": 1, "failures": []},
    ]


# -- publish stage ---------------------------------------------------------------


def test_publish_report_and_manifest_for_local_dir(settings):
    settings.feed_token = TOKEN
    out = settings.publish_dir / TOKEN
    audio = settings.morning_dir / "staged.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(_FRAME * 100)  # 14_400 bytes

    report = publish_local(settings, {"date": DATE, "duration_seconds": 12.6},
                           audio)
    assert set(report) == {"feed_url", "episode_url", "artwork_url"}
    # no feed_base_url: the local archive hands back file URIs
    assert report == {
        "feed_url": (out / "feed.xml").as_uri(),
        "episode_url": (out / "audio" / f"{DATE}.mp3").as_uri(),
        "artwork_url": (out / "artwork.png").as_uri(),
    }
    assert (out / "audio" / f"{DATE}.mp3").read_bytes() == _FRAME * 100

    manifest = json.loads((out / "episodes.json").read_text())
    assert len(manifest) == 1
    assert set(manifest[0]) == {"date", "file", "bytes", "duration_seconds",
                                "guid", "published_at"}
    assert {k: manifest[0][k] for k in ("date", "file", "bytes",
                                        "duration_seconds", "guid")} == {
        "date": DATE, "file": f"audio/{DATE}.mp3", "bytes": 14400,
        "duration_seconds": 12,  # int-truncated into the manifest
        "guid": f"morning-briefing-{DATE}",
    }
    published_at = datetime.fromisoformat(manifest[0]["published_at"])
    # stamped in the listener's morning timezone (Asia/Hong_Kong, +08:00)
    assert published_at.utcoffset() == timedelta(hours=8)

    # with a public base URL the report switches to the production URL form
    settings.feed_base_url = "https://briefing.pages.dev"
    other = publish_local(settings, {"date": "2026-09-21",
                                     "duration_seconds": 300}, audio)
    public = f"https://briefing.pages.dev/{TOKEN}"
    assert other == {"feed_url": f"{public}/feed.xml",
                     "episode_url": f"{public}/audio/2026-09-21.mp3",
                     "artwork_url": f"{public}/artwork.png"}
    manifest = json.loads((out / "episodes.json").read_text())
    assert [e["date"] for e in manifest] == [DATE, "2026-09-21"]  # append-only
    assert manifest[0]["file"] == f"audio/{DATE}.mp3"  # early entry untouched


# -- the whole chain ---------------------------------------------------------------


def test_full_run_report_and_log_sequence(session, offline_chain):
    report = run_morning(session, offline_chain, date=DATE)
    assert report["date"] == DATE and report["outcome"] == "published"
    assert set(report) == {"date", "outcome", "stages", "finished_at"}
    # naive local timestamp, second resolution — the CI log's wall clock
    datetime.fromisoformat(report["finished_at"])

    assert _normalize(report["stages"], offline_chain.home) == {
        "collect": {"sources": 1, "status": "done"},
        "digest": {"method": "extractive", "items_in_window": 3,
                   "verdict_method": "skipped:llm_not_configured"},
        "script": {"method": "extractive", "words": 141, "est_seconds": 57},
        "tts": {"engine": "edge-tts", "chunks": 14, "duration_seconds": 29.4,
                "mp3": f"<home>/morning/{DATE}/{DATE}.mp3"},
        "publish": {
            "publisher": "local-dir",
            "feed_url": f"file://<home>/morning/publish/{TOKEN}/feed.xml",
            "episode_url":
                f"file://<home>/morning/publish/{TOKEN}/audio/{DATE}.mp3",
            "artwork_url": f"file://<home>/morning/publish/{TOKEN}/artwork.png",
        },
        "notify": {"notifiers": [], "outcome": "no-op"},
    }
    # the report is the CI's machine-readable run log: it must serialize
    assert json.loads(json.dumps(report))["outcome"] == "published"

    entries = _entries(session)
    assert [a for a, _ in entries] == [
        "job_started",
        "item_created", "item_created", "item_created",
        "source_collected",
        "job_finished",
        "daily_digest_built",
        "morning_brief_built",
        "morning_audio_rendered",
        "morning_episode_published",
        "morning_notify",
        "morning_run_finished",
    ]
    by_action = {a: _normalize(d, offline_chain.home) for a, d in entries}
    assert by_action["source_collected"]["created"] == 3
    assert by_action["source_collected"]["entries"] == 3
    assert by_action["job_finished"] == {
        "job_id": 1, "status": "done",
        "totals": {"created": 3, "updated": 0, "unchanged": 0,
                   "duplicate": 0}}
    built = by_action["daily_digest_built"]
    assert built["method"] == "extractive"
    assert built["verdict_method"] == "skipped:llm_not_configured"
    assert built["items_in_window"] == 3  # the fixture feed is out of window
    assert built["verdicts"] == []
    assert by_action["morning_audio_rendered"] == {
        "date": DATE, "engine": "edge-tts", "duration_seconds": 29.4,
        "chunks": 14, "mp3": f"<home>/morning/{DATE}/{DATE}.mp3"}
    assert by_action["morning_episode_published"] == {
        "date": DATE, "publisher": "local-dir", "duration_seconds": 29.4}
    assert by_action["morning_notify"] == {
        "date": DATE, "notifiers": [], "outcome": "no-op"}
    assert by_action["morning_run_finished"] == {
        "date": DATE, "outcome": "published"}
