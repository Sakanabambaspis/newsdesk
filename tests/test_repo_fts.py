"""Full-text search over collected items, with provenance in results."""

from __future__ import annotations

from sqlmodel import select

from newsdesk.core.models import Item
from newsdesk.pipeline.runner import run_collection
from newsdesk.storage.repo import ItemRepo, SourceRepo


def _collect(session, settings, feed):
    SourceRepo(session).add(feed.as_uri(), kind="rss")
    return run_collection(session, settings)


def test_fts_phrase_search(session, settings, energy_feed, tech_feed):
    _collect(session, settings, energy_feed)
    _collect(session, settings, tech_feed)

    repo = ItemRepo(session)
    # phrase that appears only in the grid story -> original + syndicated copy
    hits = repo.search("heat wave strains", limit=10)
    assert len(hits) == 2
    assert all("Grid operator" in h.title for h in hits)

    # multi-word query falls back to OR across tokens: grid story (both copies
    # — original + syndicated) + podcast item
    or_hits = repo.search("grid podcast", limit=10)
    assert len(or_hits) == 3

    wind = repo.search("offshore wind auction", limit=10)
    assert len(wind) == 1
    assert wind[0].publisher == "Example Energy Desk"
    assert wind[0].url_canonical.endswith("offshore-wind-auction")


def test_fts_no_false_positives(session, settings, energy_feed):
    _collect(session, settings, energy_feed)
    assert ItemRepo(session).search("qxscthmvl") == []


def test_fts_word_or_fallback(session, settings, energy_feed):
    _collect(session, settings, energy_feed)
    # no phrase "rotating outages avoided prices" but words exist across items
    hits = ItemRepo(session).search("outages podcast", limit=10)
    assert hits  # OR fallback kicks in when the exact phrase matches nothing


def test_recent_ordering_and_canonical_output(session, settings, energy_feed):
    _collect(session, settings, energy_feed)
    rows = ItemRepo(session).recent(limit=10)
    assert len(rows) == 3
    canonical = rows[0].to_canonical()
    assert canonical["source"]["url"].startswith("https://")
    assert canonical["provenance"]["content_hash"]
    assert canonical["timestamps"]["retrieved_at"].endswith("+00:00") or \
        "+" in canonical["timestamps"]["retrieved_at"] or \
        canonical["timestamps"]["retrieved_at"].endswith("Z")


def test_log_is_append_only_shape(session, settings, energy_feed):
    from newsdesk.storage.repo import LogRepo
    _collect(session, settings, energy_feed)
    entries = LogRepo(session).recent(limit=100)
    actions = [e.action for e in entries]
    assert "job_started" in actions and "job_finished" in actions
    assert actions.count("item_created") == 3
    assert actions.count("source_collected") == 1
    # ids strictly increase -> append-only
    ids = [e.id for e in entries]
    assert ids == sorted(ids, reverse=True)
