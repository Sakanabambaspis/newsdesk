"""The `local-dir` publisher: proves the publish contract host-independently.

Writes the episode audio plus the whole-regenerated feed under
``<publish_dir>/<feed-token>/…`` — the 128-bit token as a path segment from
env is the only access control (wayfinder ticket 07), and it never appears
in log entries. Publish is idempotent per date (same-date republish
replaces audio, keeps the original first-published timestamp) and
append-only across dates (ticket 10): GUIDs and enclosure URLs never change
and no prune code exists. This publisher doubles as the test double for the
orchestration and rehearsal tickets and stays useful as a local archive.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .feed import FEED_OWNER_EMAIL_FALLBACK, build_feed_xml, episode_guid, write_artwork
from .registries import PUBLISHERS
from .script import MORNING_TZ


class PublishError(Exception):
    """Loud, safe-to-print publish failure (no secrets in messages)."""


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    if path.exists():
        return json.loads(path.read_text())
    return []


def publish_local(settings, episode: dict[str, Any],
                  audio_path: str | Path) -> dict[str, str]:
    """Publish one episode: place audio, upsert manifest, regenerate feed.

    ``episode``: {date, duration_seconds}. Returns the permanent
    feed/episode/artwork URLs for this host.
    """
    token = settings.feed_token
    if not token:
        raise PublishError(
            "NEWSDESK_FEED_TOKEN is not set. Generate one with: "
            'python -c "import secrets; print(secrets.token_hex(16))" — '
            "it is the feed's only access control.")
    date = episode["date"]
    out = (settings.publish_dir / token).resolve()
    audio_dir = out / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    dest = audio_dir / f"{date}.mp3"
    shutil.copyfile(audio_path, dest)

    manifest_path = out / "episodes.json"
    manifest = _load_manifest(manifest_path)
    prior = next((e for e in manifest if e["date"] == date), None)
    published_at = (prior["published_at"] if prior
                    else datetime.now(MORNING_TZ).isoformat())
    entry = {"date": date, "file": f"audio/{dest.name}",
             "bytes": dest.stat().st_size,
             "duration_seconds": int(episode["duration_seconds"]),
             "guid": episode_guid(date), "published_at": published_at}
    manifest = sorted([e for e in manifest if e["date"] != date] + [entry],
                      key=lambda e: e["date"])
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    artwork = out / "artwork.png"
    if not artwork.exists():
        write_artwork(artwork)

    # Public URLs: base-URL form when configured (production host, e.g. the
    # pages.dev domain), otherwise local file URIs for the local archive.
    base = (settings.feed_base_url or "").rstrip("/")
    if base:
        public = f"{base}/{token}"
        feed_url = f"{public}/feed.xml"
        episode_url = f"{public}/audio/{date}.mp3"
        artwork_url = f"{public}/artwork.png"
        feed_rows = [{**e, "enclosure_url": f"{public}/{e['file']}"} for e in manifest]
    else:
        feed_url = (out / "feed.xml").as_uri()
        episode_url = dest.as_uri()
        artwork_url = artwork.as_uri()
        feed_rows = [{**e, "enclosure_url": (out / e["file"]).as_uri()}
                     for e in manifest]

    xml = build_feed_xml(feed_rows, feed_url=feed_url,
                         owner_email=settings.feed_owner_email
                         or FEED_OWNER_EMAIL_FALLBACK,
                         artwork_url=artwork_url)
    (out / "feed.xml").write_text(xml, encoding="utf-8")
    return {"feed_url": feed_url, "episode_url": episode_url,
            "artwork_url": artwork_url}


PUBLISHERS.register("local-dir", publish_local)


def already_published(settings, date: str) -> bool:
    """Idempotency guard: has ``date`` been published? (Checks the manifest
    before the orchestrator does any work, so re-runs exit early.)"""
    token = settings.feed_token
    if not token:
        return False
    manifest = _load_manifest(
        (settings.publish_dir / token).resolve() / "episodes.json")
    return any(e["date"] == date for e in manifest)


publish_local.already_published = already_published
