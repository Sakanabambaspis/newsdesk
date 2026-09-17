"""Fixture tests for fetcher kinds with special plumbing:

- arXiv: the synthetic Atom API response (tests/fixtures/feeds/synthetic-arxiv.atom)
  served through a mocked HttpHelper — the only network-attached fetcher whose
  payload arrives without a robots check (DESIGN.md section 19).
- sitemap: a staged M2 kind — a *known* SOURCE_KINDS entry with NO fetcher
  registered yet, so it is accepted at add time and rejected at collect time
  (fully unknown kinds are now rejected at add time; audit finding C3 fixed).
"""

from __future__ import annotations

from pathlib import Path

import httpx

from newsdesk.config import Settings
from newsdesk.core.ids import sha256_hex
from newsdesk.core.models import Source
from newsdesk.ingest.arxiv import ArxivFetcher
from newsdesk.ingest.base import FetchError, get_fetcher
from newsdesk.ingest.fetcher import HttpHelper
from newsdesk.pipeline.runner import run_collection
from newsdesk.storage.repo import ItemRepo, SourceRepo

FEEDS = Path(__file__).parent / "fixtures" / "feeds"
ARXIV_ATOM = (FEEDS / "synthetic-arxiv.atom").read_bytes()


def _fetch_settings(home: Path) -> Settings:
    return Settings(home=home, min_request_interval=0.0)


def test_arxiv_fixture_entries_parse(tmp_path):
    settings = _fetch_settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/query"
        return httpx.Response(200, content=ARXIV_ATOM)  # no ETag header

    http = HttpHelper(settings, transport=httpx.MockTransport(handler))
    capture = ArxivFetcher().fetch(
        Source(id=1, url="arxiv://cs.LG", kind="arxiv"),
        settings, http=http)
    assert capture.status == "ok"
    assert len(capture.entries) == 2
    # multi-line <title> folds to a single-spaced one-liner
    assert capture.entries[0].title == \
        "Scaling Deliberation Improves Robot Policy Transfer"
    assert capture.entries[0].author == "A. Researcher, B. Second"
    assert capture.entries[1].author == "C. Solo"
    assert capture.entries[1].url.endswith("2609.10002v2")
    assert capture.snapshot_path.endswith(".atom")


def test_arxiv_capture_content_hash_is_payload_digest_without_etag(tmp_path):
    """Fixture finding F-4 / audit C2 (fixed): RawCapture.content_hash is the
    raw payload digest even when the response carries no ETag header."""
    settings = _fetch_settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ARXIV_ATOM)

    http = HttpHelper(settings, transport=httpx.MockTransport(handler))
    capture = ArxivFetcher().fetch(
        Source(id=1, url="arxiv://cs.LG", kind="arxiv"),
        settings, http=http)
    assert capture.content_hash == sha256_hex(ARXIV_ATOM)


def test_arxiv_items_still_dedupe_correctly_across_runs(session, settings, tmp_path):
    """Blast-radius check for C2: item identity uses the per-entry
    content_hash (title+text), not RawCapture.content_hash, so arXiv items
    dedupe to 'unchanged' on a second identical response regardless."""
    session.add(Source(url="arxiv://cs.LG", kind="arxiv", title="arXiv cs.LG"))
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ARXIV_ATOM,
                              headers={"etag": '"v1"'})

    # A fresh helper per run: run_collection closes its HttpHelper when the
    # pass ends, so sharing one instance across two runs would fail.
    import newsdesk.pipeline.runner as runner_mod
    original = runner_mod.HttpHelper
    runner_mod.HttpHelper = lambda s: HttpHelper(
        s, transport=httpx.MockTransport(handler))
    try:
        job1 = run_collection(session, settings)
        job2 = run_collection(session, settings)
    finally:
        runner_mod.HttpHelper = original

    assert job1.stats["totals"]["created"] == 2
    assert job2.stats["totals"]["unchanged"] == 2
    assert job2.stats["totals"]["created"] == 0


def test_sitemap_kind_has_no_fetcher_and_is_rejected_at_collect_time(session, settings):
    """Staged M2 kinds are accepted at add time (they are in SOURCE_KINDS)
    and fail during collection with error:no-fetcher until their fetcher
    ships. Fully unknown kinds never get this far (see test_c3)."""
    assert get_fetcher("sitemap") is None
    source, created = SourceRepo(session).add(
        (FEEDS / "synthetic-sitemap.xml").as_uri(), kind="sitemap",
        title="Sitemap publisher")
    assert created is True  # known staged kind: accepted, fetcher still staged

    job = run_collection(session, settings)
    stats = job.stats["sources"][str(source.id)]
    assert stats["error"] == "no fetcher registered for kind 'sitemap'"
    assert job.status == "partial"
    session.refresh(source)
    assert source.last_status == "error:no-fetcher"
    assert ItemRepo(session).recent(limit=10) == []
