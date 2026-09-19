"""edge-tts plugin: chunked synthesis, retries, voice fallback, loud failure.

All behavior runs through a fake engine and synthetic MP3 bytes, so the unit
tests are fully offline (DESIGN.md §17) and need no ``tts`` extra; the one
optional live check at the bottom skips when offline. MP3 bytes are synthetic
MPEG2 Layer III frames (24 kHz / 48 kbps — edge-tts's real format) so
durations are exact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsdesk.cli import app
from newsdesk.morning.edgetts import (MIN_CHUNK_BYTES, TTSError, chunk_text,
                                      edge_tts_synth, mp3_duration_seconds,
                                      mp3_probe)
from newsdesk.morning.script import make_section, write_sidecar
from newsdesk.storage.db import Database
from newsdesk.storage.repo import LogRepo

runner = CliRunner()


class NoAudioReceived(Exception):
    """Local stand-in for edge_tts.exceptions.NoAudioReceived (offline tests)."""


# MPEG2 Layer III, 24 kHz, 48 kbps: one frame = 576 samples = 0.024 s = 144 B.
_FRAME = b"\xff\xf3\x64\x00" + b"\x00" * 140
# MPEG1 Layer III, 48 kHz, 128 kbps: one frame = 1152 samples = 0.024 s = 384 B.
_FRAME_48K = b"\xff\xfb\x94\x00" + b"\x00" * 380
FRAME_SECONDS = 576 / 24000


def mp3_bytes(frames: int) -> bytes:
    return _FRAME * frames


class FakeEngine:
    """Stands in for edge_tts.Communicate.save_sync.

    ``fail_when(text, voice, attempt)`` returns an exception to raise (after
    truncating any output) or None to synthesize; ``tiny_when`` emulates a
    trivial (sub-MIN_CHUNK_BYTES) server response that must be retried.
    Attempts are tracked per (text, voice).
    """

    def __init__(self, fail_when=None, tiny_when=None):
        self.calls: list[tuple[str, str]] = []
        self.fail_when = fail_when
        self.tiny_when = tiny_when
        self._attempts: dict[tuple[str, str], int] = {}

    def __call__(self, text: str, voice: str, path: Path) -> None:
        self.calls.append((text, voice))
        key = (text, voice)
        attempt = self._attempts.get(key, 0)
        self._attempts[key] = attempt + 1
        if self.fail_when and (exc := self.fail_when(text, voice, attempt)):
            path.write_bytes(b"")
            raise exc
        if self.tiny_when and self.tiny_when(text, voice, attempt):
            path.write_bytes(b"tiny")
            return
        path.write_bytes(mp3_bytes(40 + len(text)))


def make_sidecar(settings, date="2026-09-18", sections=None) -> Path:
    sections = sections if sections is not None else [
        make_section("cold_open", "Here is your morning briefing."),
        make_section("headline", "Agents shipped a thing. It matters."),
        make_section("deep_dive", "The mechanism is novel. " * 40),
        make_section("close", "That's the briefing — the full digest has the details."),
    ]
    return write_sidecar(settings, date, {"method": "extractive", "date": date,
                                          "sections": sections,
                                          "stats": {"words": 10, "est_seconds": 4}})


@pytest.fixture
def no_pace(settings):
    settings.tts_pace = 0.0  # pacing sleeps are fake anyway; 0 isolates backoffs
    return settings


class SleepLog:
    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


# -- Duration measurement -------------------------------------------------------

def test_mp3_duration_measured_from_frames():
    assert mp3_duration_seconds(mp3_bytes(0)) == 0.0
    assert mp3_duration_seconds(mp3_bytes(100)) == pytest.approx(2.4)
    # ID3v2 header is skipped; syncsafe size 10 -> 20 junk bytes prefix
    data = b"ID3\x04\x00\x00\x00\x00\x00\x0a" + b"junk" * 5 + mp3_bytes(50)
    assert mp3_duration_seconds(data) == pytest.approx(1.2)
    assert mp3_duration_seconds(b"not mp3 at all") == 0.0


def test_chunk_text_two_sentences_never_mid_word():
    assert chunk_text("One here. Two there! Three done? A fragment") == [
        "One here. Two there!", "Three done? A fragment"]
    run_on = "word " * 300  # no sentence punctuation at all
    chunks = chunk_text(run_on)
    assert all(len(c) <= 600 for c in chunks)
    assert " ".join(chunks) == " ".join(run_on.split())  # nothing lost
    assert all(not c.startswith(" ") and not c.endswith(" ") for c in chunks)


# -- Happy path: sidecar -> complete MP3 + measured duration ---------------------

def test_sidecar_renders_complete_mp3(settings):
    sidecar = make_sidecar(settings)
    engine = FakeEngine()
    report = edge_tts_synth(settings, sidecar, engine=engine, sleep=SleepLog())

    mp3 = Path(report["mp3"])
    assert mp3 == settings.morning_dir / "2026-09-18" / "2026-09-18.mp3"
    assert mp3.exists()
    measured = mp3_duration_seconds(mp3.read_bytes())
    assert report["duration_seconds"] == pytest.approx(round(measured, 1))
    assert report["duration_seconds"] > 0
    assert report["engine"] == "edge-tts" and report["chunks"] > 0
    assert report["date"] == "2026-09-18"
    assert report["sample_rate"] == 24000  # sanity-checked, recorded for the feed
    assert report["voices"] == ["en-US-AriaNeural"]

    # chunk order follows the script: first call speaks the cold open, last
    # call the close; every call used the default (config) voice
    assert engine.calls[0][1] == "en-US-AriaNeural"
    assert engine.calls[0][0].startswith("Here is your morning briefing.")
    assert engine.calls[-1][0].startswith("That's the briefing")

    # audio sidecar lands next to the mp3 with a per-chunk manifest
    body = json.loads(Path(report["audio_json"]).read_text())
    assert len(body["manifest"]) == report["chunks"]
    assert all(m["voice"] == "en-US-AriaNeural" for m in body["manifest"])
    assert all(m["failures"] == [] for m in body["manifest"])

    # no chunk scratch dir is left behind
    leftovers = [p.name for p in mp3.parent.iterdir()
                 if "chunks" in p.name or p.name.endswith(".partial")]
    assert leftovers == []


def test_section_voice_overrides_config(settings):
    sidecar = make_sidecar(settings, sections=[
        make_section("headline", "Config voice here."),
        make_section("headline", "Section voice here.", voice="en-US-GuyNeural"),
    ])
    engine = FakeEngine()
    edge_tts_synth(settings, sidecar, engine=engine, sleep=SleepLog())
    assert engine.calls[0] == ("Config voice here.", "en-US-AriaNeural")
    assert engine.calls[1] == ("Section voice here.", "en-US-GuyNeural")


# -- Pacing -----------------------------------------------------------------------

def test_paces_between_chunks_not_before_first(settings):
    sidecar = make_sidecar(settings)  # 4 sections -> several chunks
    sleeps = SleepLog()
    report = edge_tts_synth(settings, sidecar, engine=FakeEngine(), sleep=sleeps)
    pacing = [d for d in sleeps.delays if 1.5 <= d <= 1.5 * 1.5 + 0.001]
    assert len(pacing) == report["chunks"] - 1  # between requests, never after last


# -- Retries with exponential backoff ---------------------------------------------

def test_retries_403_and_no_audio_with_backoff(no_pace):
    sidecar = make_sidecar(no_pace)
    flap = {"n": 0}

    def flaky(text, voice, attempt):
        if "mechanism" not in text:  # only the deep-dive chunk is flaky
            return None
        flap["n"] += 1
        if flap["n"] <= 2:  # the documented failure modes
            return [Exception("403 Forbidden"), NoAudioReceived("no audio")][flap["n"] - 1]
        return None

    sleeps = SleepLog()
    report = edge_tts_synth(no_pace, sidecar, engine=FakeEngine(fail_when=flaky),
                            sleep=sleeps)
    assert report["chunks"] > 0
    # exponential 2s -> 4s, plus bounded jitter (delay/4)
    assert 2.0 <= sleeps.delays[0] < 2.5
    assert 4.0 <= sleeps.delays[1] < 5.0
    body = json.loads(Path(report["audio_json"]).read_text())
    flaky_chunk = next(m for m in body["manifest"] if m["failures"])
    assert len(flaky_chunk["failures"]) == 2
    assert "403" in flaky_chunk["failures"][0]
    assert "NoAudioReceived" in flaky_chunk["failures"][1]


def test_trivial_output_is_retried(no_pace):
    sidecar = make_sidecar(no_pace, sections=[
        make_section("close", "Only a close today.")])
    engine = FakeEngine(tiny_when=lambda text, voice, attempt: attempt == 0)
    report = edge_tts_synth(no_pace, sidecar, engine=engine, sleep=SleepLog())
    assert report["chunks"] == 1
    assert len(engine.calls) == 2  # first attempt was under MIN_CHUNK_BYTES


# -- Per-chunk voice fallback -------------------------------------------------------

def test_kept_failing_voice_falls_back_per_chunk(no_pace):
    sidecar = make_sidecar(no_pace, sections=[
        make_section("cold_open", "Open stays on the primary voice."),
        make_section("close", "Close keeps failing on the primary voice."),
    ])
    sleeps = SleepLog()

    def primary_dies(text, voice, attempt):
        if voice == "en-US-AriaNeural" and "Close" in text:
            return NoAudioReceived("no audio was received")
        return None

    engine = FakeEngine(fail_when=primary_dies)
    report = edge_tts_synth(no_pace, sidecar, engine=engine, sleep=sleeps)
    body = json.loads(Path(report["audio_json"]).read_text())
    by_section = {m["section_index"]: m for m in body["manifest"]}
    assert by_section[0]["voice"] == "en-US-AriaNeural"   # primary kept
    assert by_section[1]["voice"] == "en-US-EmmaMultilingualNeural"  # alt
    assert all(c[1] != "en-US-EmmaMultilingualNeural"
               for c in engine.calls if "Open" in c[0])
    # exhausted the primary's attempts before switching: 4 retries + first = 5
    assert len([c for c in engine.calls if c[0].startswith("Close")
                and c[1] == "en-US-AriaNeural"]) == 5


# -- Loud failure: never a silent gap ------------------------------------------------

def test_lost_chunk_aborts_loudly_leaves_no_mp3(settings):
    sidecar = make_sidecar(settings, sections=[
        make_section("cold_open", "Fine here."),
        make_section("deep_dive", "This chunk is doomed. Completely doomed."),
    ])
    mp3_path = settings.morning_dir / "2026-09-18" / "2026-09-18.mp3"

    def doomed(text, voice, attempt):
        return NoAudioReceived(f"no audio for {voice}") if "doomed" in text else None

    with pytest.raises(TTSError, match="chunk never rendered.*"
                       "never published with a missing chunk"):
        edge_tts_synth(settings, sidecar, engine=FakeEngine(fail_when=doomed),
                       sleep=SleepLog())
    assert not mp3_path.exists()          # no silent gap, no partial episode
    assert list(mp3_path.parent.glob("*.partial")) == []
    leftovers = [p for p in mp3_path.parent.iterdir() if "chunks" in p.name]
    assert leftovers == []


def test_undecodable_stitch_aborts(settings):
    sidecar = make_sidecar(settings, sections=[make_section("close", "Garbage.")])

    def garbage(text, voice, path):  # big enough to pass the size check
        path.write_bytes(b"\x00" * (MIN_CHUNK_BYTES + 10))

    with pytest.raises(TTSError, match="no decodable MP3 frames"):
        edge_tts_synth(settings, sidecar, engine=garbage, sleep=SleepLog())
    mp3 = settings.morning_dir / "2026-09-18" / "2026-09-18.mp3"
    assert not mp3.exists()


def test_missing_sidecar_is_loud(settings):
    with pytest.raises(TTSError, match="newsdesk script"):
        edge_tts_synth(settings, settings.morning_dir / "2026-09-18"
                       / "2026-09-18-script.json", engine=FakeEngine())


def test_script_with_no_text_is_loud(settings):
    sidecar = make_sidecar(settings, sections=[make_section("close", "")])
    with pytest.raises(TTSError, match="no speakable text"):
        edge_tts_synth(settings, sidecar, engine=FakeEngine())


# -- Sanity: mixed-rate chunks would decode as corrupt audio ----------------------

def test_chunks_disagreeing_sample_rate_abort(settings):
    sidecar = make_sidecar(settings, sections=[
        make_section("headline", "One sentence here. Two there. Three.")])

    def mixed(text, voice, path):
        path.write_bytes(_FRAME_48K * 40 if text.startswith("Three")
                         else _FRAME * 40)

    with pytest.raises(TTSError, match="disagree on sample rate"):
        edge_tts_synth(settings, sidecar, engine=mixed, sleep=SleepLog())
    mp3 = settings.morning_dir / "2026-09-18" / "2026-09-18.mp3"
    assert not mp3.exists()


def test_probe_reports_sample_rate():
    assert mp3_probe(mp3_bytes(10)) == {"seconds": pytest.approx(0.24),
                                        "frames": 10, "sample_rate": 24000}
    assert mp3_probe(b"garbage") == {"seconds": 0.0, "frames": 0,
                                     "sample_rate": None}


# -- CLI surface -------------------------------------------------------------------

def test_cli_audio_renders_through_real_plugin(settings, monkeypatch):
    # patch only the network seam; the command, registry, and render path are real
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save",
                        lambda text, voice, path: path.write_bytes(
                            mp3_bytes(40 + len(text))))
    monkeypatch.setenv("NEWSDESK_HOME", str(settings.home))
    monkeypatch.setenv("NEWSDESK_TTS_PACE", "0")  # the command re-reads Settings
    make_sidecar(settings)  # sidecar date is the fixture's fixed 2026-09-18

    result = runner.invoke(app, ["audio", "--date", "2026-09-18", "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["engine"] == "edge-tts"
    assert body["duration_seconds"] > 0
    assert Path(body["mp3"]).exists()

    with Database(settings).session() as session:
        entries = [e for e in LogRepo(session).recent(limit=5)
                   if e.action == "morning_audio_rendered"]
    assert entries and entries[0].detail["chunks"] == body["chunks"]
    assert "duration_seconds" in entries[0].detail


def test_cli_audio_missing_sidecar_exits_loud(settings, monkeypatch):
    monkeypatch.setenv("NEWSDESK_HOME", str(settings.home))
    result = runner.invoke(app, ["audio", "--date", "1999-01-01"])
    assert result.exit_code == 1
    assert "no script sidecar" in result.output
    assert "newsdesk script" in result.output  # actionable: how to fix it


# -- Optional live check (skipped when offline) ---------------------------------------
# Run with: pytest tests/test_edgetts.py -k live -rs

def test_live_edge_tts_synthesis(settings):
    pytest.importorskip("edge_tts")
    sidecar = make_sidecar(settings, date="live-check", sections=[
        make_section("close", "Testing the live edge-tts synthesis check.")])
    try:
        report = edge_tts_synth(settings, sidecar)
    except TTSError as exc:  # offline, endpoint moved, DRM token rotated — skip
        pytest.skip(f"edge-tts live check unavailable: {exc}")
    assert report["duration_seconds"] > 0
    data = Path(report["mp3"]).read_bytes()
    assert len(data) > MIN_CHUNK_BYTES
    assert mp3_duration_seconds(data) == pytest.approx(
        report["duration_seconds"], abs=0.2)
