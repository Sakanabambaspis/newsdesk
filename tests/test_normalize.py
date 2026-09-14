"""Fixture feed -> fetcher -> normalizer -> canonical records."""

from __future__ import annotations

from newsdesk.config import Settings
from newsdesk.core.models import Source
from newsdesk.ingest.rss import RSSFetcher
from newsdesk.pipeline.normalize import normalize_capture, relevance_score


def _fetch(path, settings: Settings, source_id: int = 1) -> list[dict]:
    source = Source(id=source_id, url=path.as_uri(), kind="rss")
    capture = RSSFetcher().fetch(source, settings)
    assert capture.status == "ok"
    return normalize_capture(capture, source)


def test_canonical_shape(energy_feed, settings):
    items = _fetch(energy_feed, settings)
    assert len(items) == 3
    for item in items:
        assert set(item) == {"id", "source", "timestamps", "content",
                             "analysis", "provenance"}
        assert item["id"].startswith("item_")
        assert set(item["provenance"]) >= {"content_hash", "extraction_method",
                                           "url_canonical", "snapshot_path", "simhash"}
        assert item["provenance"]["extraction_method"] == "rss"
        assert item["timestamps"]["retrieved_at"]
        assert item["analysis"]["relevance"] is None  # no watchlist terms yet


def test_full_content_extracted_and_html_stripped(energy_feed, settings):
    items = _fetch(energy_feed, settings)
    grid = next(i for i in items if "Grid operator" in i["content"]["title"])
    text = grid["content"]["text"]
    assert "level-two emergency" in text
    assert "<p>" not in text
    assert "ignore_me" not in text  # script content must not leak


def test_summary_only_entry(energy_feed, settings):
    items = _fetch(energy_feed, settings)
    wind = next(i for i in items if "Offshore wind" in i["content"]["title"])
    assert "record bids" in wind["content"]["text"]


def test_enclosure_becomes_media(energy_feed, settings):
    items = _fetch(energy_feed, settings)
    podcast = next(i for i in items if "podcast" in i["content"]["title"])
    assert podcast["content"]["media"] == [{
        "url": "https://cdn.example.com/audio/summer-outlook-ep12.mp3",
        "type": "audio/mpeg", "length": "24000000",
    }]


def test_tracking_params_do_not_split_identity(energy_feed, settings):
    items = _fetch(energy_feed, settings)
    grid = next(i for i in items if "Grid operator" in i["content"]["title"])
    assert grid["provenance"]["url_canonical"] == \
        "https://energy.example.com/stories/grid-emergency"


def test_snapshot_written(energy_feed, settings):
    items = _fetch(energy_feed, settings)
    from pathlib import Path
    snap = Path(items[0]["provenance"]["snapshot_path"])
    assert snap.exists() and snap.stat().st_size > 0


def test_relevance_score_with_terms(energy_feed, settings):
    items = _fetch(energy_feed, settings)
    grid = next(i for i in items if "Grid operator" in i["content"]["title"])
    scored = relevance_score(grid["content"]["title"], grid["content"]["text"],
                             [("grid", 1.0), ("completely absent term", 1.0)])
    assert scored == 0.5
