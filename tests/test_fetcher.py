"""Remote fetch path over a mocked transport: politeness, conditional GET, 304."""

from __future__ import annotations

import httpx

from newsdesk.core.models import Source
from newsdesk.ingest.fetcher import HttpHelper
from newsdesk.ingest.rss import RSSFetcher

ROBOTS_ALLOW = "User-agent: *\nDisallow: /private/\n"
ROBOTS_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"

FEED_BODY = (
    '<?xml version="1.0"?><rss version="2.0"><channel>'
    "<title>Remote Feed</title>"
    "<item><title>Remote story</title>"
    "<link>https://feeds.example.com/stories/1</link>"
    "<description>Remote description text.</description></item>"
    "</channel></rss>"
)


def _helper(settings, robots: str = ROBOTS_ALLOW, etag_mode: str = "none") -> HttpHelper:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if path == "/feed.xml":
            seen["if_none_match"] = request.headers.get("if-none-match", "")
            if etag_mode == "304" and request.headers.get("if-none-match"):
                return httpx.Response(304)
            return httpx.Response(
                200, text=FEED_BODY,
                headers={"etag": '"v1"', "last-modified": "Sun, 13 Sep 2026 10:00:00 GMT"},
            )
        return httpx.Response(404)

    helper = HttpHelper(settings, transport=httpx.MockTransport(handler))
    helper._seen = seen  # type: ignore[attr-defined]
    return helper


def test_remote_fetch_parses_and_saves_conditional_headers(settings):
    source = Source(id=1, url="https://feeds.example.com/feed.xml", kind="rss")
    helper = _helper(settings)
    capture = RSSFetcher().fetch(source, settings, http=helper)
    assert capture.status == "ok"
    assert capture.meta["etag"] == '"v1"'
    assert capture.meta["feed_title"] == "Remote Feed"
    assert [e.url for e in capture.entries] == ["https://feeds.example.com/stories/1"]


def test_conditional_get_returns_not_modified(settings):
    source = Source(id=1, url="https://feeds.example.com/feed.xml", kind="rss",
                    etag='"v1"')
    helper = _helper(settings, etag_mode="304")
    capture = RSSFetcher().fetch(source, settings, http=helper)
    assert capture.status == "not_modified"
    assert capture.entries == []
    assert helper._seen["if_none_match"] == '"v1"'  # conditional header was sent


def test_robots_disallow_all_skips_fetch(settings):
    source = Source(id=1, url="https://feeds.example.com/feed.xml", kind="rss")
    helper = _helper(settings, robots=ROBOTS_DISALLOW_ALL)
    capture = RSSFetcher().fetch(source, settings, http=helper)
    assert capture.status == "skipped"
    assert capture.entries == []
    assert "robots" in capture.meta["skip_reason"]


def test_http_error_raises_fetch_error(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_ALLOW)
        return httpx.Response(404)

    source = Source(id=1, url="https://feeds.example.com/feed.xml", kind="rss")
    helper = HttpHelper(settings, transport=httpx.MockTransport(handler))
    import pytest
    from newsdesk.ingest.base import FetchError
    with pytest.raises(FetchError, match="404"):
        RSSFetcher().fetch(source, settings, http=helper)
