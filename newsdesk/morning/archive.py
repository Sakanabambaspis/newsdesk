"""The episode archive: the pre-TTS script lands in the repo (ticket 04).

The sidecar is the exact episode content — the feed deliberately carries
audio only, so content-via-subscription would mean transcription. But the
sidecar lives under ``NEWSDESK_HOME`` and evaporates with the CI runner, so
the morning run copies it verbatim into the repo (``newsdesk morning
--archive <dir>``; CI passes ``briefing``) beside a version-aware
``meta.json``: which workflow version and code commit produced the episode.

The bundle is token-free by construction — the token appears in exactly one
place, the published feed — and the private repo is not the published site,
so the sidecar's "never published" posture (morning-audio ticket 07) is
untouched. Archiving runs only on the run that publishes: an
``already_published`` re-run is a fresh runner whose sidecar is gone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .feed import episode_guid
from .script import MORNING_TZ, sidecar_path

ARCHIVE_FORMAT = "episode-archive@1"
DEFAULT_STATION_NAME = "morning-briefing"

# meta only carries per-stage facts the reports already publish; a station
# binding a workflow with renamed stages simply gets nulls here (the machine
# report stays the honest surface — cli.py)
_DIGEST_FACTS = ("method", "verdict_method", "items_in_window")
_SCRIPT_FACTS = ("method", "words", "est_seconds")
_TTS_FACTS = ("engine", "chunks", "duration_seconds")


class ArchiveError(Exception):
    """Loud, safe-to-print archive failure (no secrets in messages)."""


def _stage_facts(report: dict[str, Any], stage: str,
                 facts: tuple[str, ...]) -> dict[str, Any]:
    stage_report = report["stages"].get(stage) or {}
    return {name: stage_report.get(name) for name in facts}


def _code_commit() -> str | None:
    """Best-effort commit of the code that produced the episode: the
    Actions run's checkout SHA, else the local checkout's HEAD."""
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10,
                              check=True)
    except Exception:  # provenance is best-effort by design
        return None
    return done.stdout.strip() or None


def write_bundle(settings: Any, report: dict[str, Any], *,
                 workflow_name: str, workflow_version: int,
                 archive_dir: str | Path,
                 station: str | None = None) -> dict[str, Any]:
    """Copy the sidecar and write the version-aware meta for one episode.

    ``station`` is the run's station exactly as passed to the engine (the
    sidecar layout follows it); ``report`` is ``run_workflow``'s report and
    must be a published run. Returns the bundle summary for the run report.
    """
    if report.get("outcome") != "published":
        raise ArchiveError("only a published run can be archived")
    date = report["date"]
    run_station = station or report.get("station")
    name = run_station or DEFAULT_STATION_NAME

    sidecar = sidecar_path(settings, date, run_station)
    if not sidecar.exists():
        raise ArchiveError(f"script sidecar not found: {sidecar}")

    meta = {
        "format": ARCHIVE_FORMAT,
        "date": date,
        "station": name,
        "guid": episode_guid(date, name),
        "workflow": {"name": workflow_name, "version": workflow_version},
        "code_commit": _code_commit(),
        "digest": _stage_facts(report, "digest", _DIGEST_FACTS),
        "script": _stage_facts(report, "script", _SCRIPT_FACTS),
        "tts": _stage_facts(report, "tts", _TTS_FACTS),
        "episode_path": f"audio/{date}.mp3",
        "archived_at": datetime.now(MORNING_TZ).isoformat(),
    }
    payload = json.dumps(meta, indent=2, ensure_ascii=False)
    token = settings.feed_token
    if token and token in payload:  # mechanically enforced no-leak posture
        raise ArchiveError("bundle would carry the feed token — refusing")

    bundle = Path(archive_dir) / name / date
    bundle.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sidecar, bundle / "script.json")
    (bundle / "meta.json").write_text(payload + "\n", encoding="utf-8")
    return {"path": str(bundle), "station": name, "date": date}
