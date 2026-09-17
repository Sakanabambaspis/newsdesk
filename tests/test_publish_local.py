"""Local-dir publisher + anonymous feed: idempotency, append, stability."""

from __future__ import annotations

import json
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import pytest

from newsdesk.config import Settings
from newsdesk.morning.feed import ARTWORK_SIZE, episode_guid
from newsdesk.morning.publish import PublishError, publish_local
from newsdesk.morning.registries import PUBLISHERS, load_plugins

TOKEN = "3f9a1c77b2d84e05a1c3f6d2b9e08417"  # 128-bit hex, like production
HKT = ZoneInfo("Asia/Hong_Kong")


@pytest.fixture
def publisher_settings(settings: Settings, monkeypatch) -> Settings:
    monkeypatch.setattr(settings, "feed_token", TOKEN)
    return settings


def _audio(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(b"ID3" + payload)  # minimal fake MP3
    return path


def _publish(settings, tmp_path: Path, date: str, *, payload: bytes = b"x",
             duration: int = 300) -> dict:
    return publish_local(settings, {"date": date, "duration_seconds": duration},
                         _audio(tmp_path, f"{date}-{len(payload)}.mp3", payload))


def _feed(out: Path):
    return feedparser.parse((out / "feed.xml").read_text())


def _raw(settings: Settings) -> str:
    return (_out(settings) / "feed.xml").read_text()


def _out(settings: Settings) -> Path:
    return settings.publish_dir / TOKEN


# -- registry -----------------------------------------------------------------


def test_local_dir_publisher_registered():
    load_plugins()
    assert PUBLISHERS.get("local-dir").__name__ == "publish_local"


# -- happy path: shape of the feed --------------------------------------------


def test_publish_writes_feed_under_token_path(publisher_settings, tmp_path):
    result = _publish(publisher_settings, tmp_path, "2026-09-18")

    out = _out(publisher_settings)
    assert (out / "feed.xml").exists()
    assert (out / "audio" / "2026-09-18.mp3").exists()
    assert TOKEN in result["feed_url"] and TOKEN in result["episode_url"]

    d = _feed(out)
    assert d.bozo == 0  # parses clean as XML/RSS
    assert d.feed.title == "Morning Briefing"
    assert d.feed.author == "Morning Briefing"  # pseudonymous
    assert len(d.entries) == 1
    entry = d.entries[0]
    assert entry.title == "2026-09-18"  # date-only title
    assert entry.id == episode_guid("2026-09-18")
    assert entry.enclosures[0]["type"] == "audio/mpeg"
    assert int(entry.enclosures[0]["length"]) > 0
    assert int(entry.itunes_duration) == 300
    raw = _raw(publisher_settings)
    assert "itunes:image" in raw and "itunes:owner" in raw  # Apple requirements


def test_artwork_ships_at_apple_minimum(publisher_settings, tmp_path):
    _publish(publisher_settings, tmp_path, "2026-09-18")
    png = (_out(publisher_settings) / "artwork.png").read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    assert width == height == ARTWORK_SIZE


# -- idempotency + append + stability ------------------------------------------


def test_same_date_republish_is_idempotent(publisher_settings, tmp_path):
    first = _publish(publisher_settings, tmp_path, "2026-09-18",
                     payload=b"first-run-audio", duration=290)
    d1 = _feed(_out(publisher_settings))
    first_pubdate = d1.entries[0].published
    guid1 = d1.entries[0].id
    url1 = d1.entries[0].enclosures[0]["href"]

    _publish(publisher_settings, tmp_path, "2026-09-18",
             payload=b"replaced-audio", duration=310)
    d2 = _feed(_out(publisher_settings))
    assert len(d2.entries) == 1  # replaced, not duplicated
    assert d2.entries[0].id == guid1  # GUID unchanged
    assert d2.entries[0].published == first_pubdate  # first-published kept
    assert d2.entries[0].enclosures[0]["href"] == url1
    assert int(d2.entries[0].itunes_duration) == 310  # metadata refreshed
    manifest = json.loads((_out(publisher_settings) / "episodes.json").read_text())
    assert len(manifest) == 1


def test_new_dates_append_with_stable_urls_and_guids(publisher_settings, tmp_path):
    _publish(publisher_settings, tmp_path, "2026-09-17")
    d1 = _feed(_out(publisher_settings))
    old = d1.entries[0]
    guid, url = old.id, old.enclosures[0]["href"]

    _publish(publisher_settings, tmp_path, "2026-09-18")
    _publish(publisher_settings, tmp_path, "2026-09-16")  # out-of-order arrival
    d = _feed(_out(publisher_settings))
    assert [e.title for e in d.entries] == ["2026-09-18", "2026-09-17", "2026-09-16"]
    by_date = {e.title: e for e in d.entries}
    assert by_date["2026-09-17"].id == guid  # GUID never changes
    assert by_date["2026-09-17"].enclosures[0]["href"] == url  # URL permanent
    assert all(e.id == episode_guid(e.title) for e in d.entries)  # never reused


def test_feed_lists_every_episode_no_pruning(publisher_settings, tmp_path):
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for n in range(40):
        date = (base + timedelta(days=n)).strftime("%Y-%m-%d")
        _publish(publisher_settings, tmp_path, date)
    d = _feed(_out(publisher_settings))
    assert len(d.entries) == 40  # append-only; a prune step is a later add-on


# -- anonymity (ticket 07) ------------------------------------------------------


def test_feed_metadata_stays_anonymous(tmp_path):
    # the world contains identifying material; none of it may reach the XML.
    # Base-URL mode (production posture): clean public URLs, no local paths.
    from newsdesk.config import Settings
    home = tmp_path / "home"
    s = Settings(home=home, feed_token=TOKEN,
                 feed_base_url="https://briefing.pages.dev")
    s.ensure_dirs()

    _publish(s, tmp_path, "2026-09-18")
    raw = _raw(s).lower()
    for leak in ("agents", "private-project", "item_", "show notes", "digest",
                 "sakana", "pytest", "@gmail", "deep dive", "headline",
                 "file://", str(home).lower()):
        assert leak not in raw, f"feed leaks '{leak}'"
    d = _feed(_out(s))
    assert not [e for e in d.entries if e.get("description")]  # no show notes
    assert d.feed.description == "A short daily technology briefing."
    assert d.entries[0].enclosures[0]["href"].startswith(
        f"https://briefing.pages.dev/{TOKEN}/audio/")


def test_owner_email_is_an_alias(publisher_settings, tmp_path):
    publisher_settings.feed_owner_email = "alias@someproviders.invalid"
    _publish(publisher_settings, tmp_path, "2026-09-18")
    assert "alias@someproviders.invalid" in _raw(publisher_settings)
    publisher_settings.feed_owner_email = None
    _publish(publisher_settings, tmp_path, "2026-09-18")
    raw = _raw(publisher_settings)
    assert "itunes:email" in raw and "@" in raw  # Apple requires an owner email


def test_missing_token_fails_loudly(settings, tmp_path, monkeypatch):
    settings.feed_token = None
    with pytest.raises(PublishError, match="NEWSDESK_FEED_TOKEN"):
        _publish(settings, tmp_path, "2026-09-18")
