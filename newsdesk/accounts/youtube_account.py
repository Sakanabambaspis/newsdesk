"""YouTube account: sync subscriptions into newsdesk sources.

This account kind is a watchlist synthesizer, not an item fetcher: it lists
the channel's public subscriptions via Data API v3 (API key only — no OAuth)
and registers each subscribed channel as a ``youtube`` source, which the
video fetcher then collects like any hand-added channel. Registration is
idempotent (SourceRepo.add dedupes on url+kind) and every action is logged
with the account as actor.

Credential envs: ``NEWSDESK_YOUTUBE_API_KEY`` +
``NEWSDESK_YOUTUBE_CHANNEL_ID`` (your channel; subscriptions must be public
unless you use OAuth later). Capability: ``read_subscriptions``.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..storage.repo import LogRepo, SourceRepo
from .base import AccountError
from .manager import AccountManager

API_BASE = "https://www.googleapis.com/youtube/v3"


def api_get(path: str, params: dict[str, str], api_key: str) -> dict[str, Any]:
    """Seam for tests and the CLI probe: one GET against Data API v3."""
    import httpx

    response = httpx.get(API_BASE + path, params={**params, "key": api_key},
                         timeout=30.0)
    if response.status_code >= 400:
        raise AccountError(f"YouTube API HTTP {response.status_code} for {path}: "
                           f"{response.text[:200]}")
    return response.json()


def test_connection(session: AccountSession) -> str:
    """Read-only probe behind `newsdesk accounts test` (one subscriptions page)."""
    api_get("/subscriptions",
            {"part": "snippet", "channelId": session.credentials["channel_id"],
             "maxResults": "1"},
            session.credentials["api_key"])
    return "connection ok"


def _iter_subscriptions(api_key: str, channel_id: str) -> list[dict[str, Any]]:
    subscriptions: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params = {"part": "snippet", "channelId": channel_id, "maxResults": "50"}
        if page_token:
            params["pageToken"] = page_token
        payload = api_get("/subscriptions", params, api_key)
        subscriptions.extend(payload.get("items") or [])
        page_token = payload.get("nextPageToken")
        if not page_token or len(subscriptions) >= 500:
            break
    return subscriptions


def sync_subscriptions(session, settings: Settings, account_ref: str = "default") -> dict[str, Any]:
    """Register every public subscription of the linked channel as a source."""
    manager = AccountManager(settings)
    account = manager.connect("youtube-account", account_ref, "read_subscriptions")
    creds = account.credentials

    subscriptions = _iter_subscriptions(creds["api_key"], creds["channel_id"])

    source_repo = SourceRepo(session)
    log_repo = LogRepo(session)
    registered, existing = 0, 0
    for sub in subscriptions:
        snippet = sub.get("snippet") or {}
        channel = (snippet.get("resourceId") or {}).get("channelId")
        if not channel:
            continue
        title = snippet.get("title") or channel
        _, created = source_repo.add(
            f"https://www.youtube.com/channel/{channel}",
            kind="youtube", title=title, publisher=title)
        if created:
            registered += 1
            log_repo.append("source_added", {
                "url": f"https://www.youtube.com/channel/{channel}",
                "kind": "youtube", "title": title, "via": "subscriptions-sync",
            }, actor=f"account:{account.account_ref}")
        else:
            existing += 1

    log_repo.append("account_synced", {
        "account": account.account_ref, "provider": "youtube-account",
        "subscriptions_seen": len(subscriptions),
        "sources_registered": registered, "sources_already_present": existing,
    }, actor=f"account:{account.account_ref}")
    return {"account": account.account_ref, "subscriptions": len(subscriptions),
            "registered": registered, "already_present": existing}
