"""The `edge-tts` TTS engine: sidecar Script -> complete MP3 + measured duration.

Per the wayfinder ticket 04 findings (docs/research/tts-and-llm-in-ci.md,
including the live smoke test run on this machine), the service behind
edge-tts fails intermittently and in waves — handshake 403s (Oct 2024),
NoAudioReceived (Dec 2025 fix, still intermittently open) — so synthesis is
chunked by section with pacing between requests, each chunk retries with
exponential backoff, and a chunk that keeps failing is retried on the
alternate voice. A chunk that survives nothing aborts the run loudly: an
episode is never rendered with a silent gap. The final MP3 is sanity-checked
(decodes as MP3, non-zero measured duration) before it replaces any previous
render; duration is measured from the actual frames, because the publish
stage writes it into the feed.

edge-tts is an optional dependency (`pip install 'newsdesk[tts]'`); the
network seam is the ``engine`` callable, so unit tests fake it entirely.
"""

from __future__ import annotations

import json
import os
import random
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .registries import TTS_ENGINES
from .script import MORNING_TZ

MIN_CHUNK_BYTES = 512  # a real synthesis of a sentence is orders of magnitude larger
CHUNK_SENTENCES = 2  # research: ~1-2 sentence chunks, far under any length cap
CHUNK_MAX_CHARS = 600  # and short enough to dodge the old 10:00-cutoff class of bugs
BACKOFF_BASE = 2.0  # retry 1 sleeps 2s, then 4, 8, 16 — research: "2 s -> 32 s"
BACKOFF_CAP = 32.0

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class TTSError(Exception):
    """Loud TTS failure; safe to print (contains script text, never secrets)."""


# -- MP3 frame walk: measured duration, not a word-count estimate --------------

_V1_L3_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_V2_L3_BITRATES = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
_SAMPLE_RATES = {3: (44100, 48000, 32000),   # MPEG1
                 2: (22050, 24000, 16000),   # MPEG2  (edge-tts: 24 kHz)
                 0: (11025, 12000, 8000)}    # MPEG2.5


def _frame_at(data: bytes, pos: int) -> tuple[int, int, int] | None:
    """Parse one MP3 frame header -> (samples_per_frame, sample_rate, length)."""
    if pos + 4 > len(data) or data[pos] != 0xFF or data[pos + 1] & 0xE0 != 0xE0:
        return None
    b1, b2 = data[pos + 1], data[pos + 2]
    version = (b1 >> 3) & 0x03          # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
    if version == 1 or (b1 >> 1) & 0x03 != 1:  # reserved version / not Layer III
        return None
    br_idx, sr_idx = (b2 >> 4) & 0x0F, (b2 >> 2) & 0x03
    if br_idx in (0, 15) or sr_idx == 3:
        return None
    samples = 1152 if version == 3 else 576
    bitrate = (_V1_L3_BITRATES if version == 3 else _V2_L3_BITRATES)[br_idx] * 1000
    sample_rate = _SAMPLE_RATES[version][sr_idx]
    length = samples * bitrate // (sample_rate * 8) + ((b2 >> 1) & 0x01)
    return samples, sample_rate, length


def mp3_probe(data: bytes) -> dict[str, Any]:
    """Walk MP3 frames -> {"seconds", "frames", "sample_rate"} (re-syncs on junk)."""
    pos, seconds, frames, sample_rate = 0, 0.0, 0, None
    if data[:3] == b"ID3" and len(data) >= 10:
        pos = 10 + ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14
                    | (data[8] & 0x7F) << 7 | (data[9] & 0x7F))
    while pos + 4 <= len(data):
        frame = _frame_at(data, pos)
        if frame is None:
            pos += 1  # resync byte-by-byte; bounded by file length
            continue
        samples, rate, length = frame
        seconds += samples / rate
        frames += 1
        sample_rate = sample_rate or rate
        pos += max(length, 4)
    return {"seconds": seconds, "frames": frames, "sample_rate": sample_rate}


def mp3_duration_seconds(data: bytes) -> float:
    """Measured duration of MP3 bytes — frames counted, never words estimated."""
    return mp3_probe(data)["seconds"]


# -- Chunking -------------------------------------------------------------------

def chunk_text(text: str) -> list[str]:
    """Split speakable prose into ~1-2 sentence chunks (never mid-word)."""
    text = " ".join(text.split())
    if not text:
        return []
    sentences: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        while len(sentence) > CHUNK_MAX_CHARS:  # pathological run-on sentence
            cut = sentence.rfind(" ", 0, CHUNK_MAX_CHARS)
            cut = cut if cut > 0 else CHUNK_MAX_CHARS
            sentences.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        sentences.append(sentence)
    chunks: list[str] = []
    group: list[str] = []
    for sentence in sentences:
        joined = " ".join(group + [sentence])
        if group and (len(group) >= CHUNK_SENTENCES or len(joined) > CHUNK_MAX_CHARS):
            chunks.append(" ".join(group))
            group = [sentence]
        else:
            group.append(sentence)
    if group:
        chunks.append(" ".join(group))
    return chunks


# -- The engine seam ------------------------------------------------------------

def _edge_save(text: str, voice: str, path: Path) -> None:
    """One synthesis request: the only network call in the module."""
    try:
        import edge_tts
    except ImportError as exc:  # optional extra, like media/vision
        raise TTSError("edge-tts is not installed — run: "
                       "pip install 'newsdesk[tts]'") from exc
    edge_tts.Communicate(text, voice).save_sync(str(path))


# -- Retry / fallback / render --------------------------------------------------

def _render_chunk(engine: Callable[..., None], chunk: dict[str, Any], path: Path,
                  *, attempts: int,
                  sleep: Callable[[float], None]) -> tuple[str, list[str]]:
    """Render one chunk; retries with exponential backoff, then alternate voice.

    The broad catch is deliberate: the service's failure modes (403,
    NoAudioReceived, timeouts, websocket resets) surface as different exception
    types across edge-tts releases (research 1.6: break-then-fix cycles), and
    an unattended run wants the retry, not the traceback. Every failure is
    recorded and reported when the chunk is finally lost.
    """
    failures: list[str] = []
    for voice in chunk["voices"]:
        for attempt in range(attempts):
            try:
                engine(chunk["text"], voice, path)
                size = path.stat().st_size if path.exists() else 0
                if size < MIN_CHUNK_BYTES:
                    raise TTSError(f"trivial output ({size} bytes)")
                return voice, failures
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
                if attempt < attempts - 1:
                    delay = min(BACKOFF_BASE * 2 ** attempt, BACKOFF_CAP)
                    sleep(delay + random.uniform(0, delay / 4))  # backoff + jitter
    raise TTSError(
        f"chunk never rendered after {attempts} attempt(s) per voice "
        f"({', '.join(chunk['voices'])}); last failure: {failures[-1]}. "
        "Aborting: an episode is never published with a missing chunk.")


def _voices_for(section: dict[str, Any], settings: Any) -> list[str]:
    """Primary voice per the script contract (config-stamped at script time),
    plus the config alternate; config is also the fallback for hand-made
    sidecars whose sections carry no voice."""
    primary = section.get("voice") or settings.morning_voice
    voices = [primary]
    if settings.morning_voice_alt and settings.morning_voice_alt not in voices:
        voices.append(settings.morning_voice_alt)
    return voices


def _plan(sections: list[dict[str, Any]], settings: Any) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for s_index, section in enumerate(sections):
        for c_index, text in enumerate(chunk_text(section.get("text") or "")):
            plan.append({"section_index": s_index, "section_type": section.get("type"),
                         "chunk_index": c_index, "text": text,
                         "voices": _voices_for(section, settings)})
    return plan


def edge_tts_synth(settings: Any, script_path: str | Path, *, engine: Any = None,
                   sleep: Callable[[float], None] | None = None) -> dict[str, Any]:
    """Render the sidecar script into ``<morning_dir>/<date>/<date>.mp3``.

    Returns the report the publish stage consumes: ``{date, mp3,
    duration_seconds, ...}`` and writes the ``<date>-audio.json`` sidecar
    (never published) with the per-chunk manifest. Missing sidecar, a lost
    chunk, or undecodable output raise :class:`TTSError` — loudly.
    """
    engine = engine or _edge_save
    sleep = sleep or time.sleep
    script_path = Path(script_path)
    try:
        payload = json.loads(script_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TTSError(f"no script sidecar at {script_path} — "
                       "run `newsdesk script` first") from exc
    except json.JSONDecodeError as exc:
        raise TTSError(f"script sidecar {script_path} is not valid JSON") from exc
    date = payload.get("date") or script_path.parent.name
    plan = _plan(payload.get("sections") or [], settings)
    if not plan:
        raise TTSError(f"script sidecar {script_path} has no speakable text")

    out_dir = script_path.parent
    mp3_path = out_dir / f"{date}.mp3"
    attempts = settings.tts_retries + 1
    pace = settings.tts_pace
    manifest: list[dict[str, Any]] = []
    rates: set[int] = set()
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=f"{date}-chunks-") as tmp:
        chunk_dir = Path(tmp)
        for i, chunk in enumerate(plan):
            if i and pace > 0:
                sleep(pace + random.uniform(0, pace / 2))  # stays inside 1–3 s
            path = chunk_dir / f"chunk-{i:03d}.mp3"
            used, failures = _render_chunk(engine, chunk, path,
                                           attempts=attempts, sleep=sleep)
            probe = mp3_probe(path.read_bytes())
            if probe["frames"] == 0:
                raise TTSError(f"chunk {i} ({chunk['section_type']}) produced "
                               f"{path.stat().st_size} bytes with no decodable MP3 "
                               "frames — refusing to ship a broken episode")
            rates.add(probe["sample_rate"])
            manifest.append({**{k: chunk[k] for k in
                                ("section_index", "section_type", "chunk_index")},
                             "voice": used, "words": len(chunk["text"].split()),
                             "failures": failures})
        data = b"".join((chunk_dir / f"chunk-{i:03d}.mp3").read_bytes()
                        for i in range(len(plan)))
    if len(rates) > 1:  # mixed-rate chunks would decode as corrupt audio
        raise TTSError(f"chunks for {date} disagree on sample rate ({sorted(rates)}) "
                       "— refusing to stitch them into one episode")
    duration = mp3_probe(data)["seconds"]
    if duration <= 0:
        raise TTSError(f"stitched audio for {date} has no decodable MP3 frames "
                       f"({len(data)} bytes) — refusing to emit a broken episode")
    partial = mp3_path.with_name(mp3_path.name + ".partial")
    partial.write_bytes(data)
    os.replace(partial, mp3_path)  # a failed render never leaves a half MP3

    report: dict[str, Any] = {
        "date": date, "engine": "edge-tts", "mp3": str(mp3_path),
        "duration_seconds": round(duration, 1), "chunks": len(plan),
        "voices": sorted({m["voice"] for m in manifest}),
        "sample_rate": sorted(rates)[0],
    }
    sidecar = out_dir / f"{date}-audio.json"
    sidecar.write_text(json.dumps(
        {"generated_at": datetime.now(MORNING_TZ).isoformat(), **report,
         "manifest": manifest}, indent=2), encoding="utf-8")
    report["audio_json"] = str(sidecar)
    return report


TTS_ENGINES.register("edge-tts", edge_tts_synth, stage="render")
