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

W4 (ticket 10): with the CI matrix each station leg deploys alone, so a
*station* deploy stages the union of all stations' archives — every known
station's manifest and audio (the legacy root's included) is fetched back
and restaged, and the one archive-intact refusal is widened to every
station. The station list rides the episode payload (the engine resolves
it from the stations table; the CI leg seeds first). Station-less runs
keep the single-manifest flow byte-identical.
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

from .feed import (FEED_OWNER_EMAIL_FALLBACK, build_feed_xml, episode_guid,
                   write_artwork)
from .publish import PublishError, _subtree
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


def _fetch_manifest(public: str, *, label: str | None = None) -> list[dict[str, Any]]:
    """Fetch one station's published manifest; empty on 404 (first deploy),
    loud on anything that would redeploy blind or truncate history."""
    where = f" ({label})" if label else ""
    status, body = _http_get(f"{public}/episodes.json")
    if status == 200 and body.strip():
        try:
            manifest = json.loads(body)
        except json.JSONDecodeError as exc:
            raise PublishError("published episodes.json is not valid JSON — "
                               "refusing to redeploy over it") from exc
        return manifest
    if status in (200, 404):
        return []
    raise PublishError(f"unexpected HTTP {status} fetching the published "
                       f"manifest{where} — refusing to redeploy blind")


def already_published(settings: Any, date: str,
                      station: dict[str, Any] | None = None) -> bool:
    """Guard for the orchestrator, per (station, date) (ticket 10): only
    that station's own manifest is consulted. A check that cannot complete
    (offline, no config) returns False — the publish itself stays
    idempotent."""
    if not (settings.feed_base_url and settings.feed_token):
        return False
    segment = (station or {}).get("path_segment")
    url = f"{settings.feed_base_url.rstrip('/')}/{settings.feed_token}" \
          + (f"/{segment}" if segment else "") + "/episodes.json"
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


def _public_url(settings: Any, segment: str | None) -> str:
    return (f"{settings.feed_base_url.rstrip('/')}/{settings.feed_token}"
            + (f"/{segment}" if segment else ""))


def publish_cloudflare(settings: Any, episode: dict[str, Any],
                       audio_path: str | Path) -> dict[str, str]:
    """Ship the episode + full archive to the Pages project, idempotently."""
    base = _require(settings.feed_base_url,
                    "NEWSDESK_FEED_BASE_URL (e.g. https://morning-briefing.pages.dev)").rstrip("/")
    _require(settings.feed_token, "NEWSDESK_FEED_TOKEN")
    _require(settings.cf_api_token, "NEWSDESK_CLOUDFLARE_API_TOKEN")
    _require(settings.cf_account_id, "NEWSDESK_CLOUDFLARE_ACCOUNT_ID")
    _require(settings.cf_project, "NEWSDESK_CLOUDFLARE_PROJECT")
    if episode.get("station") is not None:
        return _publish_station_union(settings, episode, audio_path)
    date = episode["date"]
    public = f"{base}/{settings.feed_token}"

    manifest = _fetch_manifest(public)

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


def _publish_station_union(settings: Any, episode: dict[str, Any],
                           audio_path: str | Path) -> dict[str, str]:
    """A station run's deploy (ticket 10): a Pages deployment is the
    complete site, so this leg fetches back and restages the union of all
    stations' archives — the new episode lands only in the publishing
    station's own manifest; every other station is carried verbatim (its
    audio fetched back, its feed regenerated from its own manifest and
    identity). Any station's history that cannot be fetched refuses the
    deploy — the archive-intact posture, widened."""
    date = episode["date"]
    me = {"name": episode["station"],
          "path_segment": episode.get("path_segment"),
          "feed": episode.get("feed") or {}}
    stations = episode.get("stations") or [me]
    if not any(s["name"] == me["name"] for s in stations):
        stations = [*stations, me]
    with tempfile.TemporaryDirectory(prefix="morning-cf-") as tmp:
        site = Path(tmp) / settings.feed_token
        for target in stations:
            segment = target.get("path_segment")
            public = _public_url(settings, segment)
            label = f"station '{target['name']}'" if segment else None
            manifest = _fetch_manifest(public, label=label)
            root = _subtree(site, segment)
            audio_dir = root / "audio"
            audio_dir.mkdir(parents=True)
            mine = target["name"] == me["name"]
            if mine:
                dest = audio_dir / f"{date}.mp3"
                shutil.copyfile(audio_path, dest)
                prior = next((e for e in manifest if e["date"] == date), None)
                entry = {"date": date, "file": f"audio/{date}.mp3",
                         "bytes": dest.stat().st_size,
                         "duration_seconds": int(episode["duration_seconds"]),
                         "guid": episode_guid(date, station=me["name"]),
                         "published_at": (prior["published_at"] if prior else
                                          datetime.now(MORNING_TZ).isoformat())}
                manifest = sorted(
                    [e for e in manifest if e["date"] != date] + [entry],
                    key=lambda e: e["date"])
            for e in manifest:
                if mine and e["date"] == date:
                    continue
                st, content = _http_get(f"{public}/{e['file']}")
                if st != 200 or len(content) < _MIN_AUDIO_BYTES:
                    raise PublishError(
                        f"station '{target['name']}' episode {e['date']} "
                        f"could not be fetched from the live site (HTTP "
                        f"{st}) — refusing to redeploy: the deployment "
                        "would drop it from the archive")
                (audio_dir / f"{e['date']}.mp3").write_bytes(content)
            (root / "episodes.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8")
            write_artwork(root / "artwork.png")
            identity = target.get("feed") or {}
            feed_rows = [{**e, "enclosure_url": f"{public}/{e['file']}"}
                         for e in manifest]
            (root / "feed.xml").write_text(
                build_feed_xml(feed_rows, feed_url=f"{public}/feed.xml",
                               owner_email=identity.get("owner_email")
                               or settings.feed_owner_email
                               or FEED_OWNER_EMAIL_FALLBACK,
                               artwork_url=f"{public}/artwork.png",
                               identity=identity or None),
                encoding="utf-8")
        _wrangler_deploy(Path(tmp), settings)
    public = _public_url(settings, me["path_segment"])
    return {"feed_url": f"{public}/feed.xml",
            "episode_url": f"{public}/audio/{date}.mp3",
            "artwork_url": f"{public}/artwork.png"}


PUBLISHERS.register("cloudflare-pages", publish_cloudflare,
                   stage="publish")
publish_cloudflare.already_published = already_published
