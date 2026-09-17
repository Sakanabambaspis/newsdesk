"""Collection runner: fetch -> normalize -> upsert -> log, per source.

One Job row per run; per-source stats land in job.stats and in the
append-only log. A failing source never aborts the run; a failing item
never aborts its source (DESIGN.md section 4).
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from .. import accounts as _accounts  # noqa: F401  -- side-effect import: registers email/twitter fetchers
from ..config import Settings
from ..core.models import Job
from ..ingest.base import FetchError, get_fetcher
from ..ingest.fetcher import HttpHelper
from ..storage.repo import ItemRepo, JobRepo, LogRepo, SourceRepo, WatchlistRepo
from .normalize import normalize_capture


def _actor(capture) -> str:
    """Authenticated fetches log under the account they acted as."""
    ref = capture.meta.get("account_ref")
    return f"account:{ref}" if ref else "system"


def _empty_source_stats() -> dict[str, Any]:
    return {"created": 0, "updated": 0, "unchanged": 0, "duplicate": 0,
            "skipped": False, "not_modified": False, "error": None}


def run_collection(session: Session, settings: Settings,
                   source_ids: list[int] | None = None) -> Job:
    source_repo = SourceRepo(session)
    item_repo = ItemRepo(session)
    job_repo = JobRepo(session)
    log_repo = LogRepo(session)
    watchlist_repo = WatchlistRepo(session)

    job = job_repo.start()
    log_repo.append("job_started", {"job_id": job.id, "source_ids": source_ids})

    sources = source_repo.list(enabled_only=True)
    if source_ids is not None:
        wanted = set(source_ids)
        sources = [s for s in sources if s.id in wanted]

    include_terms = watchlist_repo.include_terms()
    http = HttpHelper(settings)

    all_stats: dict[str, Any] = {str(s.id): _empty_source_stats() for s in sources}
    try:
        for source in sources:
            stats = all_stats[str(source.id)]
            fetcher_cls = get_fetcher(source.kind)
            if fetcher_cls is None:
                stats["error"] = f"no fetcher registered for kind '{source.kind}'"
                source_repo.record_fetch(source, status="error:no-fetcher")
                log_repo.append("source_error", {"source_id": source.id, "url": source.url,
                                                 "error": stats["error"]})
                continue
            try:
                capture = fetcher_cls().fetch(source, settings, http=http)
            except FetchError as exc:
                stats["error"] = str(exc)[:500]
                source_repo.record_fetch(source, status=f"error:{str(exc)[:200]}")
                log_repo.append("source_error", {"source_id": source.id, "url": source.url,
                                                 "error": stats["error"]})
                continue
            except Exception as exc:  # never let one source kill the job
                stats["error"] = f"unexpected: {exc}"[:500]
                source_repo.record_fetch(source, status="error:unexpected")
                log_repo.append("source_error", {"source_id": source.id, "url": source.url,
                                                 "error": stats["error"]})
                continue

            if capture.status == "not_modified":
                stats["not_modified"] = True
                source_repo.record_fetch(source, status="not_modified")
                log_repo.append("source_not_modified", {"source_id": source.id, "url": source.url})
                continue
            if capture.status == "skipped":
                stats["skipped"] = True
                reason = capture.meta.get("skip_reason", "unknown")
                source_repo.record_fetch(source, status=f"skipped:{reason}")
                log_repo.append("source_skipped", {"source_id": source.id, "url": source.url,
                                                   "reason": reason})
                continue

            try:
                items = normalize_capture(capture, source, include_terms)
                for item in items:
                    outcome, row = item_repo.upsert(item, source_id=source.id)
                    stats[outcome] += 1
                    if outcome == "created":
                        log_repo.append("item_created", {"item_id": row.id, "url": row.url,
                                                         "publisher": row.publisher,
                                                         "source_id": source.id})
                    elif outcome == "updated":
                        log_repo.append("item_revised", {"item_id": row.id, "url": row.url,
                                                         "revision": row.revision,
                                                         "source_id": source.id})
            except Exception as exc:  # a failing item/parse never aborts the source
                stats["error"] = f"unexpected: {exc}"[:500]
                source_repo.record_fetch(source, status="error:unexpected")
                log_repo.append("source_error", {"source_id": source.id, "url": source.url,
                                                 "error": stats["error"]})
                continue

            source_repo.record_fetch(source, status="ok", etag=capture.meta.get("etag"),
                                     last_modified=capture.meta.get("last_modified"))
            log_repo.append("source_collected", {
                "source_id": source.id, "url": source.url,
                "entries": len(capture.entries), **stats,
            }, actor=_actor(capture))
    finally:
        http.close()

    totals = {
        name: sum(s.get(name, 0) for s in all_stats.values())
        for name in ("created", "updated", "unchanged", "duplicate")
    }
    job = job_repo.finish(job, stats={"totals": totals, "sources": all_stats})
    log_repo.append("job_finished", {"job_id": job.id, "status": job.status,
                                     "totals": totals})
    return job
