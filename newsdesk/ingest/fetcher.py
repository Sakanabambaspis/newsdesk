"""Polite HTTP helper shared by all network fetchers.

Implements the politeness contract from docs/DESIGN.md section 5.2:
identified user agent, robots.txt compliance, per-host rate limiting,
conditional GET, bounded retries. Local files (file:// or plain paths,
used by fixtures and manual imports) bypass all of it.
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import httpx
from urllib.robotparser import RobotFileParser

from ..config import Settings


class FetchStatusError(Exception):
    """A retryable HTTP status (e.g. 5xx) persisted across all attempts."""


class HttpHelper:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.client = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    # -- robots ---------------------------------------------------------

    def _load_robots(self, host: str) -> RobotFileParser | None:
        if host in self._robots:
            return self._robots[host]
        parser = RobotFileParser()
        robots_url = f"https://{host}/robots.txt"
        try:
            response = self.client.get(robots_url)
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
            else:
                parser.parse([])  # no rules published -> allow
        except httpx.HTTPError:
            parser = None  # unreachable: proceed permissively, log at caller
        self._robots[host] = parser
        return parser

    def allowed(self, url: str) -> tuple[bool, str]:
        parts = urlsplit(url)
        host = parts.netloc
        if not host:
            return False, "no host"
        parser = self._load_robots(host)
        if parser is None:
            return True, "robots unreachable"
        if getattr(parser, "disallow_all", False):
            return False, "robots disallow-all"
        ua = self.settings.user_agent
        if parser.can_fetch(ua, url) and parser.can_fetch("*", url):
            return True, "ok"
        return False, "disallowed by robots.txt"

    # -- rate limiting + retries ----------------------------------------

    def _rate_limit(self, host: str) -> None:
        interval = self.settings.min_request_interval
        last = self._last_hit.get(host)
        if last is not None:
            wait = interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(min(wait, 30.0))
        self._last_hit[host] = time.monotonic()

    def get(self, url: str, *, etag: str | None = None,
            last_modified: str | None = None) -> httpx.Response:
        """GET with politeness. Raises httpx.HTTPError after bounded retries."""
        parts = urlsplit(url)
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        attempts = self.settings.max_retries + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            self._rate_limit(parts.netloc)
            try:
                response = self.client.get(url, headers=headers)
                if response.status_code == 429 and attempt < attempts - 1:
                    # Throttled: honor Retry-After (capped), then back off.
                    retry_after = response.headers.get("retry-after")
                    delay = min(float(retry_after), 60.0) if retry_after and \
                        retry_after.replace(".", "", 1).isdigit() else 1.5 ** attempt
                    time.sleep(delay)
                    continue
                if response.status_code >= 500 and attempt < attempts - 1:
                    last_error = FetchStatusError(response.status_code)
                    time.sleep(1.5 ** attempt)
                    continue
                return response
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < attempts - 1:
                    time.sleep(1.5 ** attempt)
        assert last_error is not None
        raise last_error

    def close(self) -> None:
        self.client.close()


def resolve_local(url: str) -> Path | None:
    """Return a local file path for file:// URIs and bare paths, else None."""
    parts = urlsplit(url)
    if parts.scheme == "file":
        return Path(url2pathname(parts.path))
    if parts.scheme == "":
        return Path(url)
    return None


def read_local(path: Path) -> bytes:
    return path.read_bytes()
