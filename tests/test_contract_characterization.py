"""Contract tests for the violations found in the 2026-09-16 design audit
(docs/reviews/design-audit-20260916.md, findings C1-C5). Originally written
as characterization tests pinning the broken behavior; the 2026-09-17
cleaning pass fixed each finding and flipped these assertions to the
expected contracts. C2 is additionally covered with real fixtures in
tests/test_fixture_staged_kinds.py.

All tests are offline and deterministic.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from sqlmodel import select

from newsdesk.config import Settings
from newsdesk.core.ids import sha256_hex
from newsdesk.core.models import Job, Source
from newsdesk.ingest.arxiv import ArxivFetcher
from newsdesk.ingest.base import get_fetcher
from newsdesk.ingest.fetcher import HttpHelper
from newsdesk.storage.repo import SourceRepo

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_c1_importing_storage_db_registers_all_tables(tmp_path):
    """Audit C1 (fixed): storage/db.py imports the models itself, so a fresh
    process that builds Database without importing a repo first still
    creates the full schema."""
    code = ("import newsdesk.storage.db, newsdesk.config, pathlib, tempfile; "
            "s = newsdesk.config.Settings(home=pathlib.Path(tempfile.mkdtemp())); "
            "db = newsdesk.storage.db.Database(s); "
            "import sqlmodel; "
            "print(len(sqlmodel.SQLModel.metadata.tables))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True, check=True, cwd=REPO_ROOT)
    # sources, watchlists, watchlist_terms, watchlist_sources, items, jobs,
    # log_entries, workflows, workflow_versions (the W2 catalog, ticket 06),
    # rubrics, rubric_versions (the rubric catalog, ticket 09),
    # stations (W4, ticket 12)
    assert int(result.stdout.strip()) == 12


def test_c3_add_time_rejects_unknown_kind(session, settings):
    """Audit C3 (fixed): DESIGN.md stage 1 — invalid sources are rejected at
    ADD time with a ValueError naming the known kinds."""
    with pytest.raises(ValueError, match="unknown source kind 'time-machine'"):
        SourceRepo(session).add("https://x.example/f.xml", kind="time-machine")


def test_c4_normalize_crash_is_contained_and_client_closed(
        session, settings, energy_source, monkeypatch):
    """Audit C4 (fixed): a normalize/upsert crash is contained per source
    ('a failing item never aborts a source'), the run finishes partial, and
    HttpHelper.close() runs in a finally."""
    import newsdesk.pipeline.runner as runner_mod

    closed: list[str] = []

    class SpyHelper:
        def __init__(self, settings):
            pass

        def close(self):
            closed.append("closed")

    monkeypatch.setattr(runner_mod, "HttpHelper", SpyHelper)

    def boom(*args, **kwargs):
        raise RuntimeError("normalize exploded")

    monkeypatch.setattr(runner_mod, "normalize_capture", boom)

    job = runner_mod.run_collection(session, settings, [energy_source.id])

    assert closed == ["closed"]              # close() runs even on failure
    assert job.status == "partial"
    assert "unexpected: normalize exploded" in \
        job.stats["sources"][str(energy_source.id)]["error"]


def test_c4b_upsert_crash_finishes_job(session, settings, energy_source,
                                       monkeypatch):
    """Companion to C4 (fixed): a storage-layer crash mid-run is contained
    per source and the Job row reaches a terminal status, never 'running'."""
    import newsdesk.pipeline.runner as runner_mod
    import newsdesk.storage.repo as repo_mod

    def explode(self, item, source_id):
        raise KeyError("provenance")

    monkeypatch.setattr(repo_mod.ItemRepo, "upsert", explode)
    job = runner_mod.run_collection(session, settings, [energy_source.id])
    assert job.status == "partial"
    assert job.finished_at is not None


def test_c5_caption_fetch_identifies_with_configured_user_agent(monkeypatch):
    """Audit C5 (fixed): caption downloads identify with settings.user_agent
    instead of posing as a browser (DESIGN section 5.2/11)."""
    import newsdesk.ingest.video as video_mod

    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers")
        response = httpx.Response(200, text="WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n",
                                  request=httpx.Request("GET", url))
        return response

    monkeypatch.setattr(video_mod.httpx, "get", fake_get)
    text = video_mod._fetch_caption("https://caps.test/track.vtt",
                                    "NewsdeskBot/0.1 (test)")
    assert text.startswith("WEBVTT")  # raw payload; caller parses via caption_text
    assert seen["headers"] == {"User-Agent": "NewsdeskBot/0.1 (test)"}
    assert "https://caps.test/track.vtt" == seen["url"]


def test_c2_arxiv_capture_hash_is_payload_digest_even_with_etag(tmp_path):
    """Audit C2 (fixed): RawCapture.content_hash is always the raw payload
    digest; the ETag travels in meta, never in the hash."""
    feeds = REPO_ROOT / "tests" / "fixtures" / "feeds"
    payload = (feeds / "synthetic-arxiv.atom").read_bytes()
    settings = Settings(home=tmp_path, min_request_interval=0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload,
                              headers={"etag": '"etag-value-1"'})

    http = HttpHelper(settings, transport=httpx.MockTransport(handler))
    capture = ArxivFetcher().fetch(Source(id=1, url="arxiv://cs.LG",
                                          kind="arxiv"),
                                   settings, http=http)
    assert capture.content_hash == sha256_hex(payload)
    assert capture.meta["etag"] == '"etag-value-1"'
