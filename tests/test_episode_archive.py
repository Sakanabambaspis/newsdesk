"""The episode archive (ticket 04): bundle layout, version awareness,
token-free meta, loud failures, idempotent overwrite."""

from __future__ import annotations

import json

import pytest

from newsdesk.config import Settings
from newsdesk.morning.archive import ARCHIVE_FORMAT, ArchiveError, write_bundle
from newsdesk.morning.script import write_sidecar

TOKEN = "3f9a1c77b2d84e05a1c3f6d2b9e08417"  # 128-bit hex, like production
COMMIT = "10f2096a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e"


@pytest.fixture
def archiving_settings(settings: Settings, monkeypatch) -> Settings:
    monkeypatch.setattr(settings, "feed_token", TOKEN)
    monkeypatch.setenv("GITHUB_SHA", COMMIT)
    return settings


def _report(date: str = "2026-09-22", station: str | None = None) -> dict:
    report = {
        "date": date,
        "outcome": "published",
        "stages": {
            "digest": {"method": "extractive", "verdict_method": "none",
                       "items_in_window": 7},
            "script": {"method": "llm", "words": 512, "est_seconds": 205},
            "tts": {"engine": "edgetts", "chunks": 4, "duration_seconds": 210},
        },
        "finished_at": "2026-09-22T08:12:00",
    }
    if station:
        report["station"] = station
    return report


def _sidecar(settings: Settings, date: str = "2026-09-22",
             station: str | None = None) -> None:
    write_sidecar(settings, date, {
        "date": date, "method": "llm",
        "stats": {"words": 512, "est_seconds": 205},
        "sections": [{"type": "cold_open", "text": "Good morning.",
                      "item_ids": [], "voice": "v", "est_seconds": 1}],
    }, station=station)


def _write(settings: Settings, tmp_path, report: dict, *,
           station: str | None = None) -> dict:
    return write_bundle(settings, report,
                        workflow_name="default-morning", workflow_version=1,
                        archive_dir=tmp_path / "briefing", station=station)


def test_bundle_layout_and_version_aware_meta(archiving_settings, tmp_path):
    _sidecar(archiving_settings)
    summary = _write(archiving_settings, tmp_path, _report())

    bundle = tmp_path / "briefing" / "morning-briefing" / "2026-09-22"
    assert summary == {"path": str(bundle),
                       "station": "morning-briefing", "date": "2026-09-22"}
    script = json.loads((bundle / "script.json").read_text())
    assert script["date"] == "2026-09-22" and script["method"] == "llm"

    meta = json.loads((bundle / "meta.json").read_text())
    assert meta["format"] == ARCHIVE_FORMAT
    assert meta["workflow"] == {"name": "default-morning", "version": 1}
    assert meta["code_commit"] == COMMIT
    assert meta["guid"] == "morning-briefing-2026-09-22"
    assert meta["episode_path"] == "audio/2026-09-22.mp3"
    assert meta["digest"]["items_in_window"] == 7
    assert meta["script"]["words"] == 512
    assert meta["tts"]["duration_seconds"] == 210
    assert "archived_at" in meta


def test_station_runs_sidecar_and_bundle_under_the_station(
        archiving_settings, tmp_path):
    _sidecar(archiving_settings, station="papers")
    summary = _write(archiving_settings, tmp_path, _report(station="papers"),
                     station="papers")

    assert summary["station"] == "papers"
    bundle = tmp_path / "briefing" / "papers" / "2026-09-22"
    assert (bundle / "script.json").exists()
    meta = json.loads((bundle / "meta.json").read_text())
    assert meta["guid"] == "papers-2026-09-22"


def test_report_station_wins_when_caller_passes_none(archiving_settings,
                                                     tmp_path):
    # the engine stamps the station on the report; the CLI forwards the
    # requested station — either path must land in the same bundle
    _sidecar(archiving_settings, station="papers")
    _write(archiving_settings, tmp_path, _report(station="papers"))
    assert (tmp_path / "briefing" / "papers" / "2026-09-22" / "meta.json") \
        .exists()


def test_meta_never_carries_the_feed_token(archiving_settings, tmp_path):
    _sidecar(archiving_settings)
    report = _report()
    report["stages"]["tts"]["engine"] = TOKEN  # a leak attempt via facts
    with pytest.raises(ArchiveError, match="feed token"):
        _write(archiving_settings, tmp_path, report)


def test_missing_sidecar_fails_loud(archiving_settings, tmp_path):
    with pytest.raises(ArchiveError, match="sidecar not found"):
        _write(archiving_settings, tmp_path, _report())


def test_only_a_published_run_archives(archiving_settings, tmp_path):
    report = _report()
    report["outcome"] = "already_published"
    report["stages"] = {}
    with pytest.raises(ArchiveError, match="published"):
        _write(archiving_settings, tmp_path, report)


def test_rerun_overwrites_the_bundle(archiving_settings, tmp_path):
    _sidecar(archiving_settings)
    _write(archiving_settings, tmp_path, _report())
    _sidecar(archiving_settings)  # a same-date rebuild rewrites the sidecar
    _write(archiving_settings, tmp_path, _report())
    bundle = tmp_path / "briefing" / "morning-briefing" / "2026-09-22"
    script = json.loads((bundle / "script.json").read_text())
    assert script["sections"][0]["text"] == "Good morning."
