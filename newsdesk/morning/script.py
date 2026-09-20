"""The spoken-briefing contract (wayfinder ticket 08).

A Script is an ordered list of sections; each section is
``{type, text, item_ids, voice, est_seconds}`` — that shape is the decided
script-writer interface. The sidecar ``<date>-script.json`` feeds the TTS
stage and the immutable log; it is never published (wayfinder ticket 07).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WORDS_PER_MINUTE = 150  # ~750 words ≈ 5:00 at the default edge-tts rate
DEFAULT_VOICE = "en-US-AriaNeural"  # all-English script; a config line if it grates
SECTION_TYPES = ("cold_open", "headline", "deep_dive", "close")

# The morning episode is dated in the listener's morning: HKT (wayfinder 01/06).
MORNING_TZ = ZoneInfo("Asia/Hong_Kong")


def episode_date(now: datetime | None = None) -> str:
    """The episode date: today in the morning timezone, ISO ``YYYY-MM-DD``."""
    now = now or datetime.now(MORNING_TZ)
    return now.astimezone(MORNING_TZ).strftime("%Y-%m-%d")


def word_count(text: str) -> int:
    return len(text.split())


def est_seconds(text: str) -> int:
    """Estimated spoken duration of one section at the default rate."""
    return round(word_count(text) / WORDS_PER_MINUTE * 60)


def make_section(section_type: str, text: str, item_ids: list[str] | None = None,
                 voice: str = DEFAULT_VOICE) -> dict[str, Any]:
    if section_type not in SECTION_TYPES:
        raise ValueError(f"unknown section type '{section_type}' "
                         f"(known: {', '.join(SECTION_TYPES)})")
    return {"type": section_type, "text": text, "item_ids": item_ids or [],
            "voice": voice, "est_seconds": est_seconds(text)}


def clip_words(text: str, max_words: int) -> str:
    """Clip prose at a sentence boundary under ``max_words`` (never mid-word).

    Used to mechanically enforce the script budget: the model targets the
    cap, this guarantees it.
    """
    text = " ".join(text.split())
    if word_count(text) <= max_words:
        return text
    import re

    kept: list[str] = []
    total = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        count = word_count(sentence)
        if total + count > max_words:
            break
        kept.append(sentence)
        total += count
    if not kept:  # even the first sentence is over the cap: hard word cut
        return " ".join(text.split()[:max_words])
    return " ".join(kept)


def script_stats(sections: list[dict[str, Any]]) -> dict[str, int]:
    words = sum(word_count(s["text"]) for s in sections)
    return {"words": words,
            "est_seconds": sum(s["est_seconds"] for s in sections)}


def sidecar_path(settings: Any, date: str, station: str | None = None) -> Path:
    """Where the sidecar lives: station runs sidecar to
    ``<morning_dir>/<station>/<date>/<date>-script.json``; station-less
    runs keep the pinned legacy ``<morning_dir>/<date>/…`` layout
    (ticket 10 — sidecars are local artifacts, not permanent URLs)."""
    base = settings.morning_dir / station / date if station \
        else settings.morning_dir / date
    return base / f"{date}-script.json"


def write_sidecar(settings: Any, date: str, brief: dict[str, Any],
                  station: str | None = None) -> Path:
    """Persist the sidecar ``<date>-script.json`` under the run's layout."""
    path = sidecar_path(settings, date, station)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": date, "generated_at": datetime.now(MORNING_TZ).isoformat(),
               "method": brief.get("method"), "stats": brief.get("stats"),
               "sections": brief["sections"]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path
