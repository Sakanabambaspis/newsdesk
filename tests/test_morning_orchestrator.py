"""`newsdesk morning`: stages, idempotency guard, no-op notify, loud failure."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import pytest
from typer.testing import CliRunner

from newsdesk.cli import app
from newsdesk.morning.orchestrator import MorningError, run_morning
from newsdesk.storage.repo import ItemRepo, LogRepo, SourceRepo, WatchlistRepo
from tests.conftest import make_canonical_item

runner = CliRunner()

TOKEN = "3f9a1c77b2d84e05a1c3f6d2b9e08417"
# MPEG2 Layer III, 24 kHz, 48 kbps: 576 samples/frame = 0.024 s (edge-tts format).
_FRAME = b"\xff\xf3\x64\x00" + b"\x00" * 140


def _mk_item(session, source, n: int, *, title: str, text: str,
             age_hours: float, relevance: float | None = None) -> str:
    when = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    item = make_canonical_item(
        url=f"https://x.example/{n}", title=title, text=text,
        id=f"item_morningtest{n:014d}",
        timestamps={"published_at": when.isoformat(),
                    "retrieved_at": when.isoformat()},
        analysis={"relevance": relevance},
        provenance={"content_hash": f"h{n}"},
    )
    outcome, row = ItemRepo(session).upsert(item, source_id=source.id)
    assert outcome == "created"
    return row.id


@pytest.fixture
def morning_world(session):
    # a local fixture feed so the collect stage stays offline and fast
    from tests.conftest import FEEDS

    source, _ = SourceRepo(session).add((FEEDS / "sample-energy.xml").as_uri(),
                                        kind="rss")
    WatchlistRepo(session).create("dirs")
    WatchlistRepo(session).add_term(1, "agents")
    for n, rel in ((1, 0.9), (2, 0.6), (3, 0.5)):
        _mk_item(session, source, n, title=f"Agents item {n}",
                 text=f"Agents did a novel thing {n}. " * 6,
                 age_hours=2 + n, relevance=rel)
    return source


@pytest.fixture
def morning_env(settings, morning_world, monkeypatch):
    """Offline morning: fake network seam, real everything else."""
    settings.feed_token = TOKEN
    settings.tts_pace = 0.0

    def fake_edge_save(text, voice, path):
        path.write_bytes(_FRAME * (40 + len(text)))

    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", fake_edge_save)
    return settings


def _actions(session) -> dict[str, list]:
    out: dict[str, list] = {}
    for e in LogRepo(session).recent(limit=30):
        out.setdefault(e.action, []).append(e)
    return out


def _feed_path(settings) -> Path:
    return settings.publish_dir / TOKEN / "feed.xml"


def test_morning_runs_all_stages_and_publishes(session, morning_env):
    report = run_morning(session, morning_env)
    assert report["outcome"] == "published"
    stages = report["stages"]
    assert set(stages) == {"collect", "digest", "script", "tts", "publish",
                           "notify"}
    mp3 = Path(stages["tts"]["mp3"])
    assert mp3.exists() and stages["tts"]["duration_seconds"] > 0

    d = feedparser.parse(_feed_path(morning_env).read_text())
    assert d.bozo == 0 and len(d.entries) == 1
    assert d.entries[0].title == report["date"]  # the episode is in the feed

    actions = _actions(session)
    for action in ("daily_digest_built", "morning_brief_built",
                   "morning_audio_rendered", "morning_episode_published",
                   "morning_notify", "morning_run_finished"):
        assert action in actions, f"missing log entry: {action}"
    notify = actions["morning_notify"][0].detail
    assert notify == {"date": report["date"], "notifiers": [],
                      "outcome": "no-op"}  # empty registry: silent no-op
    # no token-bearing URL in any log entry (the token is the feed's only auth)
    for entries in actions.values():
        for entry in entries:
            assert TOKEN not in json.dumps(entry.detail)


def test_second_run_exits_early_without_work(session, morning_env, monkeypatch):
    run_morning(session, morning_env)
    feed_before = _feed_path(morning_env).read_text()

    calls = {"n": 0}

    def counting_save(text, voice, path):
        calls["n"] += 1
        path.write_bytes(_FRAME * 50)

    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", counting_save)
    report = run_morning(session, morning_env)
    assert report["outcome"] == "already_published"
    assert report["stages"] == {}
    assert calls["n"] == 0  # the guard ran before any synthesis work
    assert _feed_path(morning_env).read_text() == feed_before  # feed untouched


def test_unknown_engine_fails_loud_and_publishes_nothing(session, morning_env):
    morning_env.morning_tts = "nope"
    with pytest.raises(MorningError, match="selection stage failed.*"
                       "unknown TTS_ENGINES plugin 'nope'"):
        run_morning(session, morning_env)
    failed = _actions(session)["morning_run_failed"][0].detail
    assert failed["stage"] == "selection" and "nope" in failed["error"]
    assert not (morning_env.publish_dir / TOKEN).exists()  # nothing published


def test_unknown_publisher_fails_loud(session, morning_env):
    morning_env.morning_publisher = "nope"
    with pytest.raises(MorningError, match="unknown PUBLISHERS plugin 'nope'"):
        run_morning(session, morning_env)
    assert _actions(session)["morning_run_failed"][0].detail["stage"] == "selection"


def test_mid_pipeline_failure_publishes_nothing(session, morning_env, monkeypatch):
    morning_env.tts_retries = 0  # one attempt per voice: no real backoff sleeps

    def broken(text, voice, path):
        raise RuntimeError("endpoint gone")

    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", broken)
    with pytest.raises(MorningError, match="tts stage failed.*endpoint gone"):
        run_morning(session, morning_env)
    assert not (morning_env.publish_dir / TOKEN).exists()  # no partial episode
    # the script sidecar (a partial artifact) exists but no MP3 was finalized
    assert list(morning_env.morning_dir.glob("*/[0-9]*.mp3")) == []
    assert list(morning_env.morning_dir.glob("*/*.partial")) == []
    failed = _actions(session)["morning_run_failed"][0].detail
    assert failed["stage"] == "tts"


# -- CLI surface -----------------------------------------------------------------


def test_cli_morning_twice_is_idempotent(session, morning_env, monkeypatch):
    monkeypatch.setenv("NEWSDESK_HOME", str(morning_env.home))
    monkeypatch.setenv("NEWSDESK_FEED_TOKEN", TOKEN)
    monkeypatch.setenv("NEWSDESK_TTS_PACE", "0")  # the CLI builds env settings
    monkeypatch.setenv("NEWSDESK_MIN_INTERVAL", "0")

    result = runner.invoke(app, ["morning", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["outcome"] == "published"

    result = runner.invoke(app, ["morning"])
    assert result.exit_code == 0, result.output
    assert "already published" in result.output


def test_cli_morning_failure_exits_nonzero(session, morning_env, monkeypatch):
    monkeypatch.setenv("NEWSDESK_HOME", str(morning_env.home))
    monkeypatch.setenv("NEWSDESK_FEED_TOKEN", TOKEN)
    monkeypatch.setenv("NEWSDESK_MIN_INTERVAL", "0")
    monkeypatch.setenv("NEWSDESK_PUBLISHER", "nope")

    result = runner.invoke(app, ["morning"])
    assert result.exit_code == 1
    assert "unknown PUBLISHERS plugin 'nope'" in result.output
