"""cloudflare-pages publisher: whole-site deploy, archive protection, secrets.

Fully offline: the HTTP transport and the wrangler subprocess are faked, so
the tests exercise the real staging, manifest merge, archive-fetch-back, and
redaction logic. The one thing they cannot cover — a live deploy against the
real Pages project — is listed in the ticket's Comments as human setup.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from newsdesk.config import Settings
from newsdesk.morning.cloudflare import (_MIN_AUDIO_BYTES, PublishError,
                                         already_published, publish_cloudflare)
from newsdesk.morning.feed import episode_guid
from newsdesk.morning.registries import PUBLISHERS, load_plugins

TOKEN = "3f9a1c77b2d84e05a1c3f6d2b9e08417"
CF_TOKEN = "cf-secret-token-value"
BASE = "https://morning-briefing.pages.dev"
HKT = ZoneInfo("Asia/Hong_Kong")
_FRAME = b"\xff\xf3\x64\x00" + b"\x00" * 140  # 24 kHz MP3 frame, 0.024 s


def _audio(tmp_path: Path, payload: bytes) -> Path:
    path = tmp_path / f"in-{len(payload)}.mp3"
    path.write_bytes(b"ID3" + payload)
    return path


def _pub(settings, tmp_path: Path, date: str, *, duration: int = 300) -> dict:
    return publish_cloudflare(settings, {"date": date, "duration_seconds": duration},
                              _audio(tmp_path, b"x" * 2048))


def _cf_settings(settings: Settings) -> Settings:
    settings.feed_token = TOKEN
    settings.feed_base_url = BASE
    settings.cf_api_token = CF_TOKEN
    settings.cf_account_id = "acct-123"
    settings.cf_project = "morning-briefing"
    return settings


class FakeRemote:
    """The live site: per path segment ("" = the legacy root) an
    episodes.json manifest plus its audio files under <token>/[<segment>/]audio/."""

    def __init__(self, manifest=None):
        self.sites: dict[str, dict] = {
            "": {"manifest": list(manifest or []), "audio": {}}}
        self.deploys: list[dict] = []  # one entry per wrangler invocation

    @property
    def manifest(self) -> list[dict]:
        return self.sites[""]["manifest"]

    @manifest.setter
    def manifest(self, manifest) -> None:
        self.sites[""]["manifest"] = list(manifest)

    @property
    def audio(self) -> dict[str, bytes]:
        return self.sites[""]["audio"]

    def site(self, segment: str) -> dict:
        """One segment's {manifest, audio} — create/seed a sibling station."""
        return self.sites.setdefault(segment, {"manifest": [], "audio": {}})

    def serve(self, url: str) -> tuple[int, bytes]:
        rest = url[len(BASE):].strip("/").split("/")
        if not rest or rest[0] != TOKEN:
            return 404, b""
        rest = rest[1:]
        root_file = not rest or rest[0] in ("audio", "episodes.json",
                                            "feed.xml", "artwork.png")
        segment = "" if root_file else rest[0]
        rest = rest if root_file else rest[1:]
        site = self.sites.get(segment)
        if site is None:
            return 404, b""
        if rest and rest[-1] == "episodes.json":
            if not site["manifest"] and not site["audio"]:
                return 404, b""
            return 200, json.dumps(site["manifest"]).encode()
        name = rest[-1] if rest else ""
        if rest and rest[0] == "audio" and name in site["audio"]:
            return 200, site["audio"][name]
        return 404, b""

    def record_deploy(self, stage: Path, settings):
        token_dir = stage / TOKEN
        deploy: dict = {
            "files": sorted(str(p.relative_to(stage)) for p in token_dir.rglob("*")
                            if p.is_file()),
            "stations": {},
        }
        for segment in self.sites:
            root = token_dir / segment if segment else token_dir
            if not (root / "episodes.json").exists():
                continue
            deploy["stations"][segment] = {
                "feed": (root / "feed.xml").read_text(),
                "manifest": json.loads((root / "episodes.json").read_text()),
            }
        deploy.update(deploy["stations"].get("", {}))  # the root's, as before
        self.deploys.append(deploy)


@pytest.fixture
def remote(monkeypatch):
    fake = FakeRemote()
    monkeypatch.setattr("newsdesk.morning.cloudflare._http_get", fake.serve)
    monkeypatch.setattr("newsdesk.morning.cloudflare._wrangler_deploy",
                        fake.record_deploy)
    return fake


@pytest.fixture
def cf(settings):
    return _cf_settings(settings)


def _entry(date: str, duration: int = 300) -> dict:
    return {"date": date, "file": f"audio/{date}.mp3", "bytes": 2048,
            "duration_seconds": duration, "guid": episode_guid(date),
            "published_at": datetime(2026, 9, 17, 7, 30, tzinfo=HKT).isoformat()}


# -- contract + happy path -----------------------------------------------------


def test_registered_as_production_default(cf):
    load_plugins()
    assert PUBLISHERS.get("cloudflare-pages").__name__ == "publish_cloudflare"
    assert PUBLISHERS.get().__name__ == "publish_cloudflare"  # registry default


def test_first_publish_deploys_whole_site_under_token(cf, remote, tmp_path):
    result = _pub(cf, tmp_path, "2026-09-18")

    assert remote.deploys and len(remote.deploys) == 1
    deploy = remote.deploys[0]
    assert f"{TOKEN}/feed.xml" in deploy["files"]
    assert f"{TOKEN}/audio/2026-09-18.mp3" in deploy["files"]
    assert f"{TOKEN}/episodes.json" in deploy["files"]
    assert f"{TOKEN}/artwork.png" in deploy["files"]
    feed = deploy["feed"]
    assert 'guid isPermaLink="false">morning-briefing-2026-09-18' in feed
    assert f"{BASE}/{TOKEN}/audio/2026-09-18.mp3" in feed  # permanent enclosure
    assert result["feed_url"] == f"{BASE}/{TOKEN}/feed.xml"
    assert result["episode_url"] == f"{BASE}/{TOKEN}/audio/2026-09-18.mp3"
    # the feed actually points at the returned locations
    assert deploy["manifest"][0]["file"] == "audio/2026-09-18.mp3"


def test_second_day_appends_prior_audio_refetched(cf, remote, tmp_path):
    prior = _entry("2026-09-17")
    remote.manifest = [prior]
    remote.audio["2026-09-17.mp3"] = b"ID3" + _FRAME * 50

    _pub(cf, tmp_path, "2026-09-18")
    deploy = remote.deploys[0]
    assert f"{TOKEN}/audio/2026-09-17.mp3" in deploy["files"]  # archive kept
    feed = deploy["feed"]
    assert "morning-briefing-2026-09-17" in feed  # both episodes listed
    assert "morning-briefing-2026-09-18" in feed
    assert feed.index("2026-09-18") < feed.index("2026-09-17")  # newest first
    kept = next(e for e in deploy["manifest"] if e["date"] == "2026-09-17")
    assert kept["published_at"] == prior["published_at"]  # pubDate stable


def test_same_date_republish_does_not_duplicate(cf, remote, tmp_path):
    remote.manifest = [_entry("2026-09-18")]
    remote.audio["2026-09-18.mp3"] = b"ID3-old"

    _pub(cf, tmp_path, "2026-09-18")
    manifest = remote.deploys[0]["manifest"]
    assert [e["date"] for e in manifest] == ["2026-09-18"]
    assert manifest[0]["published_at"] == _entry("2026-09-18")["published_at"]


def test_unfetchable_prior_episode_refuses_to_redeploy(cf, remote, tmp_path):
    remote.manifest = [_entry("2026-09-17")]  # in manifest, no audio served
    with pytest.raises(PublishError, match="2026-09-17.*refusing to redeploy"):
        _pub(cf, tmp_path, "2026-09-18")
    assert remote.deploys == []  # nothing shipped over the archive


# -- already_published guard -----------------------------------------------------


def test_already_published_reads_remote_manifest(cf, remote, monkeypatch):
    remote.manifest = [_entry("2026-09-18")]
    assert already_published(cf, "2026-09-18") is True
    assert already_published(cf, "2026-09-19") is False


def test_already_published_fails_open(cf, monkeypatch):
    import httpx

    def boom(url):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("newsdesk.morning.cloudflare._http_get", boom)
    assert already_published(cf, "2026-09-18") is False  # publish is idempotent
    cf.feed_token = None
    assert already_published(cf, "2026-09-18") is False


# -- missing config + secret redaction --------------------------------------------


@pytest.mark.parametrize("missing", ["feed_base_url", "feed_token",
                                     "cf_api_token", "cf_account_id",
                                     "cf_project"])
def test_missing_config_fails_loudly_without_values(settings, remote, tmp_path,
                                                    missing):
    cf = _cf_settings(settings)
    setattr(cf, missing, None)
    with pytest.raises(PublishError) as excinfo:
        _pub(cf, tmp_path, "2026-09-18")
    assert CF_TOKEN not in str(excinfo.value)
    assert TOKEN not in str(excinfo.value)


def test_wrangler_failure_redacts_secrets(cf, monkeypatch, tmp_path):
    import subprocess as sp

    def fake_serve(url):
        return 404, b""

    def fake_run(cmd, capture_output, text, env, timeout):
        # wrangler's stderr echoes the env'd secrets in this nightmare scenario
        return sp.CompletedProcess(cmd, 1, stdout="",
                                   stderr=f"auth error for {CF_TOKEN} {TOKEN}")

    monkeypatch.setattr("newsdesk.morning.cloudflare._http_get", fake_serve)
    monkeypatch.setattr("newsdesk.morning.cloudflare.subprocess.run", fake_run)
    with pytest.raises(PublishError) as excinfo:
        _pub(cf, tmp_path, "2026-09-18")
    message = str(excinfo.value)
    assert "auth error" in message  # the failure surfaced, actionable
    assert CF_TOKEN not in message and TOKEN not in message  # secrets redacted


def test_wrangler_invocation_carries_secrets_in_env_not_args(cf, monkeypatch,
                                                             tmp_path):
    import subprocess as sp

    captured = {}

    def fake_serve(url):
        return 404, b""  # empty live site

    def fake_run(cmd, capture_output, text, env, timeout):
        captured["cmd"] = cmd
        captured["env"] = env
        return sp.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr("newsdesk.morning.cloudflare._http_get", fake_serve)
    monkeypatch.setattr("newsdesk.morning.cloudflare.subprocess.run", fake_run)
    _pub(cf, tmp_path, "2026-09-18")
    assert captured["cmd"][0] == "wrangler"
    assert "--project-name" in captured["cmd"]
    assert CF_TOKEN not in captured["cmd"]  # secret rides env, never argv
    assert captured["env"]["CLOUDFLARE_API_TOKEN"] == CF_TOKEN
    assert captured["env"]["CLOUDFLARE_ACCOUNT_ID"] == "acct-123"


def test_missing_wrangler_binary_is_actionable(cf, remote, monkeypatch, tmp_path):
    def no_wrangler(stage, settings):
        raise PublishError("wrangler not found — install wrangler (npm i -g "
                           "wrangler) or point NEWSDESK_WRANGLER_BIN at it")

    monkeypatch.setattr("newsdesk.morning.cloudflare._wrangler_deploy",
                        no_wrangler)
    with pytest.raises(PublishError, match="NEWSDESK_WRANGLER_BIN"):
        _pub(cf, tmp_path, "2026-09-18")


def test_published_manifest_not_json_refuses(cf, remote, monkeypatch, tmp_path):
    def serve(url):
        if url.endswith("/episodes.json"):
            return 200, b"<html>not json</html>"
        return 404, b""

    monkeypatch.setattr("newsdesk.morning.cloudflare._http_get", serve)
    with pytest.raises(PublishError, match="not valid JSON"):
        _pub(cf, tmp_path, "2026-09-18")


# -- W4: station legs stage the union of every station's archive ------------------

PAPERS = {"name": "papers", "path_segment": "papers",
          "feed": {"title": "Paper Trail", "author": "Paper Trail"}}
ROOT_STATION = {"name": "morning-briefing", "path_segment": None, "feed": {}}


def _station_episode(date: str, *, duration: int = 300) -> dict:
    return {"date": date, "duration_seconds": duration, "station": "papers",
            "path_segment": "papers", "feed": PAPERS["feed"],
            "stations": [ROOT_STATION, PAPERS]}


def test_station_deploy_restages_every_stations_archive(cf, remote, tmp_path):
    """A Pages deploy is the complete site, so the papers leg ships the
    legacy root's archive and its sibling's too — its own episode lands only
    in its own manifest."""
    remote.manifest = [_entry("2026-09-17")]
    remote.audio["2026-09-17.mp3"] = b"ID3" + _FRAME * 50
    remote.site("papers")["manifest"] = [{**_entry("2026-09-18"),
                                          "guid": "papers-2026-09-18"}]
    remote.site("papers")["audio"]["2026-09-18.mp3"] = b"ID3" + _FRAME * 50

    result = publish_cloudflare(cf, _station_episode("2026-09-19"),
                                _audio(tmp_path, b"x" * 2048))
    deploy = remote.deploys[0]
    for path in (f"{TOKEN}/episodes.json", f"{TOKEN}/audio/2026-09-17.mp3",
                 f"{TOKEN}/feed.xml", f"{TOKEN}/papers/episodes.json",
                 f"{TOKEN}/papers/audio/2026-09-18.mp3",
                 f"{TOKEN}/papers/audio/2026-09-19.mp3"):
        assert path in deploy["files"], path
    assert [e["date"] for e in deploy["stations"][""]["manifest"]] == ["2026-09-17"]
    assert [e["date"] for e in deploy["stations"]["papers"]["manifest"]] == \
        ["2026-09-18", "2026-09-19"]
    # each feed carries its own identity, and the GUIDs never collide
    assert "<title>Morning Briefing</title>" in deploy["stations"][""]["feed"]
    assert "morning-briefing-2026-09-17" in deploy["stations"][""]["feed"]
    papers_feed = deploy["stations"]["papers"]["feed"]
    assert "<title>Paper Trail</title>" in papers_feed
    assert "papers-2026-09-19" in papers_feed
    assert f"{BASE}/{TOKEN}/papers/audio/2026-09-19.mp3" in papers_feed
    assert result == {"feed_url": f"{BASE}/{TOKEN}/papers/feed.xml",
                      "episode_url": f"{BASE}/{TOKEN}/papers/audio/2026-09-19.mp3",
                      "artwork_url": f"{BASE}/{TOKEN}/papers/artwork.png"}


def test_station_deploy_refuses_when_a_sibling_archive_is_unfetchable(
        cf, remote, tmp_path):
    """The archive-intact refusal is widened: a deploy that cannot include
    every station's history never ships."""
    remote.manifest = [_entry("2026-09-17")]  # in the root manifest, no audio
    with pytest.raises(PublishError,
                       match="station 'morning-briefing'.*refusing to redeploy"):
        publish_cloudflare(cf, _station_episode("2026-09-19"),
                           _audio(tmp_path, b"x" * 2048))
    assert remote.deploys == []


def test_already_published_is_per_station(cf, remote):
    remote.manifest = [_entry("2026-09-18")]
    remote.site("papers")["manifest"] = [{**_entry("2026-09-19"),
                                          "guid": "papers-2026-09-19"}]
    assert already_published(cf, "2026-09-18", ROOT_STATION) is True
    assert already_published(cf, "2026-09-18", PAPERS) is False  # other station
    assert already_published(cf, "2026-09-19", PAPERS) is True
    assert already_published(cf, "2026-09-18") is True  # station-less = root
