"""Video fetcher tests — fully offline via the _extract_info/_fetch_caption seams."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import newsdesk.ingest.video as video_mod
from newsdesk.core.models import Source
from newsdesk.ingest.base import get_fetcher
from newsdesk.pipeline.normalize import normalize_capture
from newsdesk.storage.repo import ItemRepo, SourceRepo

VTT = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.000
Welcome back to the channel.

00:00:03.000 --> 00:00:05.000
Welcome back to the channel.
Today we cover a new paper.
"""

LISTING = {
    "id": "chan",
    "title": "Test Channel",
    "extractor_key": "YoutubeTab",
    "entries": [
        {"id": "vid1", "title": "Video One", "url": "https://www.youtube.com/watch?v=vid1"},
        {"id": "vid2", "title": "Video Two", "url": "https://www.youtube.com/watch?v=vid2"},
    ],
}

VIDEO1 = {
    "id": "vid1",
    "title": "Video One",
    "webpage_url": "https://www.youtube.com/watch?v=vid1",
    "description": "<p>A paper about <b>diffusion</b>.</p>",
    "uploader": "Test Channel",
    "upload_date": "20260901",
    "duration": 300,
    "view_count": 1234,
    "thumbnail": "https://example.invalid/thumb.jpg",
    "subtitles": {"en": [{"ext": "vtt", "url": "https://caps.test/vid1.vtt"}]},
    "automatic_captions": {},
}

VIDEO2 = {  # no captions at all -> transcript stays None
    "id": "vid2",
    "title": "Video Two",
    "webpage_url": "https://www.youtube.com/watch?v=vid2",
    "description": "No captions here.",
    "uploader": "Test Channel",
    "timestamp": 1756684800,
}


@pytest.fixture
def fake_ytdlp(monkeypatch):
    def fake_extract(url, opts):
        if opts.get("extract_flat"):
            return LISTING
        if url.endswith("vid2"):
            return {**VIDEO2, "id": "vid2", "webpage_url": url}
        return {**VIDEO1, "id": "vid1", "webpage_url": url}

    seen = {}

    def fake_caption(url, user_agent):
        seen[url] = True
        return VTT

    monkeypatch.setattr(video_mod, "_extract_info", fake_extract)
    monkeypatch.setattr(video_mod, "_fetch_caption", fake_caption)
    return seen


def _add_video_source(session, url="https://www.youtube.com/@TestChannel") -> Source:
    source, _ = SourceRepo(session).add(url, kind="youtube", title="Test Channel")
    return source


def test_fetchers_registered():
    assert get_fetcher("youtube").kind == "youtube"
    assert get_fetcher("video").kind == "video"


def test_video_fetch_extracts_entries_and_transcript(session, settings, fake_ytdlp):
    source = _add_video_source(session)
    capture = get_fetcher("youtube")().fetch(source, settings)

    assert capture.status == "ok"
    assert capture.extraction_method == "youtube"
    assert len(capture.entries) == 2
    entry = capture.entries[0]
    assert entry.title == "Video One"
    assert entry.text == "A paper about diffusion."  # html stripped
    assert entry.author == "Test Channel"
    assert entry.published_at == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert "Today we cover a new paper." in entry.transcript
    assert "Welcome back to the channel." in entry.transcript  # dedupe kept one copy
    assert capture.entries[1].transcript is None
    assert capture.snapshot_path and capture.snapshot_path.endswith(".json")
    snapshot = json.loads(open(capture.snapshot_path).read())
    assert snapshot["videos"][0]["caption_lang"] == "en"
    assert capture.meta["with_transcript"] == 1


def test_normalize_maps_video_kind_and_transcript(session, settings, fake_ytdlp):
    source = _add_video_source(session)
    capture = get_fetcher("youtube")().fetch(source, settings)
    items = normalize_capture(capture, source)
    assert items[0]["source"]["kind"] == "video"
    assert items[0]["content"]["transcript"] == capture.entries[0].transcript

    repo = ItemRepo(session)
    outcome, row = repo.upsert(items[0], source_id=source.id)
    assert outcome == "created"
    assert row.kind == "video"
    assert row.transcript
    # second collect of the same content is a no-op
    outcome2, _ = repo.upsert(items[0], source_id=source.id)
    assert outcome2 == "unchanged"


def test_bilibili_url_normalized(session, settings, fake_ytdlp):
    source, _ = SourceRepo(session).add(
        "https://space.bilibili.com/22697887/upload/video", kind="video")
    assert video_mod._fetch_url(source.url) == "https://space.bilibili.com/22697887"
    capture = get_fetcher("video")().fetch(source, settings)
    assert capture.meta["video_count"] == 2


def test_listing_failure_raises_fetch_error(session, settings, monkeypatch):
    def boom(url, opts):
        raise RuntimeError("network down")

    monkeypatch.setattr(video_mod, "_extract_info", boom)
    source = _add_video_source(session)
    with pytest.raises(video_mod.FetchError):
        get_fetcher("youtube")().fetch(source, settings)


def test_caption_parsers():
    json3 = json.dumps({"events": [
        {"segs": [{"utf8": "hello "}, {"utf8": "world"}]},
        {"segs": [{"utf8": "hello world"}]},  # repeated rolling line dropped
        {"segs": [{"utf8": "next"}]},
    ]})
    assert video_mod.caption_text(json3, "json3") == "hello world\nnext"
    srt = "1\n00:00:01,000 --> 00:00:02,000\nHi there\n"
    assert video_mod.caption_text(srt, "srt") == "Hi there"
