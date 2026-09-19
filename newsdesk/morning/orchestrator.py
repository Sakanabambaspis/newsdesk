"""One command, the whole morning (wayfinder ticket 09).

collect → digest → script → tts → publish → notify, in that order. The
idempotency guard runs first: if today's episode is already published the
run returns before doing any work, so manual re-runs can't double-publish
and a future retry fire needs no redesign (schedule ticket 06). Engines come
from the registries, selected via env; the immutable log carries one entry
per stage (provider and outcome — never key material, and never the
token-bearing URLs, which are the feed's only auth); any stage failure
aborts loudly with nothing published.

Since ticket 05 the CLI routes ``newsdesk morning`` through the workflow
engine (``newsdesk.workflow.run_workflow``), which reproduces this chain
from the ``default-morning@1`` descriptor. ``run_morning`` stays as the
characterization specimen — ``tests/test_characterization_default_chain.py``
pins it verbatim — and retires with the catalog's shipped-descriptor
bootstrap (W2); it is no longer called from production surfaces.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..pipeline.digest import build_daily_digest
from ..pipeline.runner import run_collection
from ..storage.repo import LogRepo
from .registries import (NOTIFIERS, PUBLISHERS, SCRIPTWRITERS, TTS_ENGINES,
                         load_plugins)
from .script import episode_date, write_sidecar

if TYPE_CHECKING:  # annotation-only: the runtime edge points engine → morning
    from ..workflow.engine import RunContext


class MorningError(Exception):
    """A morning stage failed; message is safe to print (no secrets)."""


def stage_script(session, settings: Any, digest: dict[str, Any],
                 date: str, *, writer: Callable[..., Any] | None = None,
                 ctx: "RunContext | None" = None
                 ) -> dict[str, Any]:
    """Run the script-writer, persist the sidecar, log the artifact.

    ``writer`` overrides the registry default (the workflow engine's
    pinned-plugin path, ticket 02); ``run_morning`` keeps resolving via
    SCRIPTWRITERS. ``ctx`` is the workflow engine's RunContext, forwarded
    only to context-native writers so they can read the repair
    bookkeeping (ticket 05); the v1 signature is unchanged without it.
    """
    writer_fn = writer or SCRIPTWRITERS.get()
    extra = {"ctx": ctx} if ctx is not None else {}
    brief = writer_fn(settings, digest, date=date, **extra)
    sidecar = write_sidecar(settings, brief["date"], brief)
    LogRepo(session).append("morning_brief_built", {
        "date": brief["date"], "method": brief["method"],
        "stats": brief["stats"],
        "sections": [{"type": s["type"], "item_ids": s["item_ids"],
                      "est_seconds": s["est_seconds"]}
                     for s in brief["sections"]],
        "sidecar": str(sidecar),
    })
    brief["sidecar"] = str(sidecar)
    return brief


def _notify(session, settings: Any, date: str,
            episode_meta: dict[str, Any]) -> dict[str, Any]:
    """Registered notifiers fire; zero registered is the designed no-op."""
    names = NOTIFIERS.names()
    for name in names:
        NOTIFIERS.get(name)(episode_meta)
    outcome = {"notifiers": names, "outcome": "sent" if names else "no-op"}
    LogRepo(session).append("morning_notify", {"date": date, **outcome})
    return outcome


def run_morning(session, settings: Any, *, date: str | None = None) -> dict[str, Any]:
    """Run the whole morning pipeline; returns the per-stage report."""
    load_plugins()  # idempotent: built-in plugins must be registered
    date = date or episode_date()
    stages: dict[str, Any] = {}
    stage = "selection"
    try:
        tts_engine = TTS_ENGINES.get(settings.morning_tts)
        publisher = PUBLISHERS.get(settings.morning_publisher)

        guard = getattr(publisher, "already_published", None)
        if guard is not None and guard(settings, date):
            LogRepo(session).append("morning_run_finished", {
                "date": date, "outcome": "already_published"})
            return {"date": date, "outcome": "already_published", "stages": {}}

        stage = "collect"
        job = run_collection(session, settings, None)
        stages["collect"] = {"sources": len(job.stats.get("sources", {})),
                             "status": job.status}

        stage = "digest"
        digest = build_daily_digest(session, settings, hours=24)
        stages["digest"] = {"method": digest.get("method"),
                            "items_in_window": digest.get("items_in_window"),
                            "verdict_method": digest.get("verdict_method")}

        stage = "script"
        brief = stage_script(session, settings, digest, date)
        stages["script"] = {"method": brief["method"], **brief["stats"]}

        stage = "tts"
        audio = tts_engine(settings, Path(brief["sidecar"]))
        LogRepo(session).append("morning_audio_rendered", {
            "date": audio["date"], "engine": audio["engine"],
            "duration_seconds": audio["duration_seconds"],
            "chunks": audio["chunks"], "mp3": audio["mp3"]})
        stages["tts"] = {"engine": audio["engine"], "chunks": audio["chunks"],
                         "duration_seconds": audio["duration_seconds"],
                         "mp3": audio["mp3"]}

        stage = "publish"
        published = publisher(settings, {"date": date,
                                         "duration_seconds": audio["duration_seconds"]},
                              audio["mp3"])
        LogRepo(session).append("morning_episode_published", {
            "date": date, "publisher": settings.morning_publisher,
            "duration_seconds": audio["duration_seconds"]})
        stages["publish"] = {"publisher": settings.morning_publisher, **published}

        stage = "notify"
        stages["notify"] = _notify(session, settings, date, {
            "date": date, "duration_seconds": audio["duration_seconds"],
            "episode_url": published["episode_url"],
            "feed_url": published["feed_url"]})
    except Exception as exc:
        LogRepo(session).append("morning_run_failed", {
            "date": date, "stage": stage, "error": str(exc)[:300]})
        raise MorningError(f"{stage} stage failed: {exc}") from exc

    LogRepo(session).append("morning_run_finished", {
        "date": date, "outcome": "published"})
    return {"date": date, "outcome": "published", "stages": stages,
            "finished_at": datetime.now().isoformat(timespec="seconds")}
