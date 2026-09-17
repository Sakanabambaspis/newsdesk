"""Twitter/X provider: home timeline via API v2 (read-only).

Uses an OAuth 2.0 user-context access token (``read`` scope) — the
documented, consented read path; never headless browser scraping.

- Source URL shape: ``x://timeline/home?account=main``.
- Incremental via ``since_id`` stored in the source's etag token
  (``x-since:<tweet_id>``); max_results capped at 100 per run.
- Author usernames come from ``expansions=author_id``; media (photos) from
  ``attachments.media_keys`` + ``includes.media``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..config import Settings
from ..core.ids import sha256_hex
from ..ingest.base import FetchError, RawCapture, RawEntry, register
from ..ingest.snapshots import save_snapshot
from .base import AccountFetcher, AccountSession

API_BASE = "https://api.x.com/2"


def api_get(path: str, params: dict[str, str], token: str) -> dict[str, Any]:
    """Seam for tests and the CLI probe: one authenticated GET against the v2 API."""
    import httpx

    response = httpx.get(API_BASE + path, params=params,
                         headers={"Authorization": f"Bearer {token}"},
                         timeout=30.0)
    if response.status_code >= 400:
        raise FetchError(f"X API HTTP {response.status_code} for {path}: "
                         f"{response.text[:200]}")
    return response.json()


def test_connection(session: AccountSession) -> str:
    """Read-only identity probe behind `newsdesk accounts test`."""
    me = api_get("/users/me", {}, session.credentials["access_token"])
    username = (me.get("data") or {}).get("username")
    return f"connected as @{username}"


def _parse_created_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@register
class TwitterFetcher(AccountFetcher):
    kind = "twitter"
    account_kind = "twitter"
    required_capability = "read_timeline"

    def _fetch(self, source, settings: Settings, session: AccountSession) -> RawCapture:
        token = session.credentials["access_token"]
        fetched_at = datetime.now(timezone.utc)

        since_id = None
        if (source.etag or "").startswith("x-since:"):
            since_id = source.etag.split(":", 1)[1].strip() or None

        me = api_get("/users/me", {}, token)
        user_id = (me.get("data") or {}).get("id")
        if not user_id:
            raise FetchError("X API: could not resolve authenticated user")

        params: dict[str, str] = {
            "max_results": "100",
            "tweet.fields": "created_at,author_id,entities",
            "expansions": "author_id",
            "user.fields": "username",
            "media.fields": "url",
        }
        if since_id:
            params["since_id"] = since_id
        payload = api_get(f"/users/{user_id}/timelines/reverse_chronological",
                          params, token)

        users = {u.get("id"): u.get("username", "unknown")
                 for u in (payload.get("includes") or {}).get("users", [])}
        media_by_key = {m.get("media_key"): m
                        for m in (payload.get("includes") or {}).get("media", [])}

        entries: list[RawEntry] = []
        for tweet in reversed(payload.get("data") or []):  # oldest first
            tweet_id = tweet.get("id")
            text = (tweet.get("text") or "").strip()
            if not tweet_id or not text:
                continue
            username = users.get(tweet.get("author_id"), "i")
            media: list[dict[str, Any]] = []
            for key in ((tweet.get("attachments") or {}).get("media_keys") or []):
                item = media_by_key.get(key) or {}
                if item.get("url"):
                    media.append({"url": item["url"], "type": item.get("type")})
            entries.append(RawEntry(
                url=f"https://x.com/{username}/status/{tweet_id}",
                title=" ".join(text.split())[:140],
                text=text,
                published_at=_parse_created_at(tweet.get("created_at")),
                author=f"@{username}",
                media=media,
            ))
        entries = entries[: settings.max_items_per_feed]

        newest = max((int(t.get("id", 0)) for t in payload.get("data") or []), default=0)
        raw = json.dumps({"account": session.account_ref, "timeline": payload},
                         ensure_ascii=False, indent=1).encode("utf-8")

        return RawCapture(
            source_url=source.url,
            fetched_at=fetched_at,
            content_hash=sha256_hex(raw),
            extraction_method=self.kind,
            snapshot_path=save_snapshot(settings, source_id=source.id,
                                        payload=raw, ext="x.json",
                                        when=fetched_at),
            entries=entries,
            status="ok",
            content_kind="post",
            meta={
                "etag": f"x-since:{newest}" if newest else (source.etag or ""),
                "tweets": len(entries),
            },
        )
