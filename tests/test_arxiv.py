"""arXiv API fetcher tests — offline via mock transport, plus robots-bypass check."""

from __future__ import annotations

import httpx
import pytest

from newsdesk.ingest.arxiv import ArxivFetcher, _parse_source_category
from newsdesk.ingest.base import FetchError, get_fetcher
from newsdesk.ingest.fetcher import HttpHelper
from newsdesk.storage.repo import SourceRepo

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>arXiv cs.LG</title>
  <entry>
    <id>http://arxiv.org/abs/2609.01234v1</id>
    <updated>2026-09-14T17:59:59Z</updated>
    <published>2026-09-14T17:59:59Z</published>
    <title>Scaling Test-Time Compute for Robot Politeness</title>
    <summary>We study test-time compute scaling for agents. Results improve
    with more deliberation and mixture-of-experts routing.</summary>
    <author><name>A. Researcher</name></author>
    <author><name>B. Second</name></author>
    <link href="http://arxiv.org/abs/2609.01234v1" rel="alternate" type="text/html"/>
  </entry>
</feed>
"""


@pytest.fixture
def arxiv_source(session):
    source, _ = SourceRepo(session).add("arxiv://cs.LG", kind="arxiv",
                                        title="arXiv cs.LG")
    return source


def test_registered_and_url_parsing():
    assert get_fetcher("arxiv").kind == "arxiv"
    assert _parse_source_category("arxiv://cs.LG") == "cs.LG"
    with pytest.raises(FetchError):
        _parse_source_category("https://example.com/rss")


def test_arxiv_fetch_via_mock(session, settings, arxiv_source, monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, content=ATOM)

    # No robots.txt handler: if the fetcher consults robots it will crash.
    http = HttpHelper(settings, transport=httpx.MockTransport(handler))
    capture = ArxivFetcher().fetch(arxiv_source, settings, http=http)

    assert seen["path"] == "/api/query"
    assert seen["query"]["search_query"] == "cat:cs.LG"
    assert seen["query"]["sortBy"] == "submittedDate"
    assert "NewsdeskBot" in seen["ua"]  # identified agent per arXiv etiquette
    assert capture.status == "ok"
    assert capture.extraction_method == "arxiv"
    assert len(capture.entries) == 1
    entry = capture.entries[0]
    assert entry.url == "http://arxiv.org/abs/2609.01234v1"
    assert entry.title.startswith("Scaling Test-Time Compute")
    assert "mixture-of-experts" in entry.text
    assert entry.author == "A. Researcher, B. Second"
    assert entry.published_at is not None
    assert capture.snapshot_path and capture.snapshot_path.endswith(".atom")


def test_arxiv_http_error_is_fetch_error(settings, arxiv_source):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="overloaded")

    http = HttpHelper(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError, match="HTTP 503"):
        ArxivFetcher().fetch(arxiv_source, settings, http=http)
