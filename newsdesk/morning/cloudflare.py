"""The `cloudflare-pages` publisher (wayfinder ticket 02) — the real host.

One `wrangler pages deploy` ships the whole site: every episode so far plus
the regenerated feed, artwork, and manifest, under the feed token's path
segment. Secrets ride env only and are redacted from any error output; the
token-bearing URLs are returned to the caller and never logged.

The live Pages project holds the only copy of past episodes (delivery
ticket 05), and a Pages deployment is the *complete* site — so before
redeploying, every prior episode is fetched back from the live site. A
deployment that cannot include the full history is refused rather than
silently trimming the archive (retention ticket 10: append-only, no prune).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .feed import FEED_OWNER_EMAIL_FALLBACK, build_feed_xml, episode_guid, write_artwork
from .publish import PublishError
from .registries import PUBLISHERS
from .script import MORNING_TZ

_DEPLOY_TIMEOUT_S = 600
_MIN_AUDIO_BYTES = 512  # same sanity floor as the TTS stage


def _require(value: str | None, what: str) -> str:
    if not value:
        raise PublishError(f"{what} is not set")
    return value


def _redact(text: str, settings: Any) -> str:
    for secret in (settings.cf_api_token, settings.feed_token):
        if secret:
            text = text.replace(secret, "***")
    return text


def _http_get(url: str) -> tuple[int, bytes]:
    """Anonymous GET — possession of the tokenized URL is the only auth."""
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        response = client.get(url)
        return response.status_code, response.content


def _wrangler_deploy(stage_dir: Path, settings: Any) -> None:
    cmd = [settings.wrangler_bin, "pages", "deploy", str(stage_dir),
           "--project-name", settings.cf_project, "--branch", "main",
           "--commit-dirty", "true"]
    env = {**os.environ,
           "CLOUDFLARE_API_TOKEN": settings.cf_api_token,
           "CLOUDFLARE_ACCOUNT_ID": settings.cf_account_id}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                env=env, timeout=_DEPLOY_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise PublishError(f"{settings.wrangler_bin} not found — install "
                           "wrangler (npm i -g wrangler) or point "
                           "NEWSDESK_WRANGLER_BIN at it") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-400:]
        raise PublishError("wrangler pages deploy failed: "
                           + _redact(tail, settings))


def already_published(settings: Any, date: str) -> bool:
    """Guard for the orchestrator. A check that cannot complete (offline,
    no config) returns False — the publish itself stays idempotent per date."""
    if not (settings.feed_base_url and settings.feed_token):
        return False
    url = f"{settings.feed_base_url.rstrip('/')}/{settings.feed_token}/episodes.json"
    try:
        status, body = _http_get(url)
    except httpx.HTTPError:
        return False
    if status != 200:
        return False
    try:
        manifest = json.loads(body)
    except json.JSONDecodeError:
        return False
    return any(e.get("date") == date for e in manifest)


def publish_cloudflare(settings: Any, episode: dict[str, Any],
                       audio_path: str | Path) -> dict[str, str]:
    """Ship the episode + full archive to the Pages project, idempotently."""
    base = _require(settings.feed_base_url,
                    "NEWSDESK_FEED_BASE_URL (e.g. https://morning-briefing.pages.dev)").rstrip("/")
    _require(settings.feed_token, "NEWSDESK_FEED_TOKEN")
    _require(settings.cf_api_token, "NEWSDESK_CLOUDFLARE_API_TOKEN")
    _require(settings.cf_account_id, "NEWSDESK_CLOUDFLARE_ACCOUNT_ID")
    _require(settings.cf_project, "NEWSDESK_CLOUDFLARE_PROJECT")
    date = episode["date"]
    public = f"{base}/{settings.feed_token}"

    status, body = _http_get(f"{public}/episodes.json")
    if status == 200 and body.strip():
        try:
            manifest = json.loads(body)
        except json.JSONDecodeError as exc:
            raise PublishError("published episodes.json is not valid JSON — "
                               "refusing to redeploy over it") from exc
    elif status in (200, 404):
        manifest = []
    else:
        raise PublishError(f"unexpected HTTP {status} fetching the published "
                           "manifest — refusing to redeploy blind")

    with tempfile.TemporaryDirectory(prefix="morning-cf-") as tmp:
        site = Path(tmp) / settings.feed_token
        audio_dir = site / "audio"
        audio_dir.mkdir(parents=True)
        dest = audio_dir / f"{date}.mp3"
        shutil.copyfile(audio_path, dest)

        prior = next((e for e in manifest if e["date"] == date), None)
        entry = {"date": date, "file": f"audio/{date}.mp3",
                 "bytes": dest.stat().st_size,
                 "duration_seconds": int(episode["duration_seconds"]),
                 "guid": episode_guid(date),
                 "published_at": (prior["published_at"] if prior else
                                  datetime.now(MORNING_TZ).isoformat())}
        manifest = sorted([e for e in manifest if e["date"] != date] + [entry],
                          key=lambda e: e["date"])

        for e in manifest:
            if e["date"] == date:
                continue
            st, content = _http_get(f"{public}/{e['file']}")
            if st != 200 or len(content) < _MIN_AUDIO_BYTES:
                raise PublishError(
                    f"prior episode {e['date']} could not be fetched from the "
                    f"live site (HTTP {st}) — refusing to redeploy: the "
                    "deployment would drop it from the archive")
            (audio_dir / f"{e['date']}.mp3").write_bytes(content)

        (site / "episodes.json").write_text(json.dumps(manifest, indent=2),
                                            encoding="utf-8")
        write_artwork(site / "artwork.png")
        feed_rows = [{**e, "enclosure_url": f"{public}/{e['file']}"}
                     for e in manifest]
        (site / "feed.xml").write_text(
            build_feed_xml(feed_rows, feed_url=f"{public}/feed.xml",
                           owner_email=settings.feed_owner_email
                           or FEED_OWNER_EMAIL_FALLBACK,
                           artwork_url=f"{public}/artwork.png"),
            encoding="utf-8")
        _wrangler_deploy(Path(tmp), settings)

    return {"feed_url": f"{public}/feed.xml",
            "episode_url": f"{public}/audio/{date}.mp3",
            "artwork_url": f"{public}/artwork.png"}


PUBLISHERS.register("cloudflare-pages", publish_cloudflare,
                   stage="publish")
publish_cloudflare.already_published = already_published
