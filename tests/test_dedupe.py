"""Tier-1 dedup paths via ItemRepo.upsert: created / unchanged / revised / duplicate."""

from __future__ import annotations

from sqlmodel import select

from newsdesk.core.models import Item
from newsdesk.pipeline.runner import run_collection
from newsdesk.storage.repo import ItemRepo, SourceRepo
from tests.conftest import make_canonical_item


def test_created_then_unchanged(session, settings, energy_source):
    repo = ItemRepo(session)
    item = make_canonical_item()
    outcome, row = repo.upsert(item, source_id=energy_source.id)
    assert outcome == "created" and row.revision == 0

    outcome, row = repo.upsert(item, source_id=energy_source.id)
    assert outcome == "unchanged" and row.revision == 0


def test_revision_on_changed_text(session, settings, energy_source):
    repo = ItemRepo(session)
    repo.upsert(make_canonical_item(), source_id=energy_source.id)

    revised = make_canonical_item(text="Body text, corrected figures")
    outcome, row = repo.upsert(revised, source_id=energy_source.id)
    assert outcome == "updated"
    assert row.revision == 1
    assert "corrected figures" in row.text


def test_syndicated_duplicate_across_urls(session, settings, energy_source):
    repo = ItemRepo(session)
    _, first = repo.upsert(make_canonical_item(url="https://a.example.com/wire"),
                           source_id=energy_source.id)
    outcome, second = repo.upsert(make_canonical_item(url="https://b.example.com/wire"),
                                  source_id=energy_source.id)
    assert outcome == "duplicate"
    assert second.duplicate_of == first.id  # kept, linked — never dropped


def test_distinct_content_is_not_duplicate(session, settings, energy_source):
    repo = ItemRepo(session)
    repo.upsert(make_canonical_item(), source_id=energy_source.id)
    outcome, _ = repo.upsert(
        make_canonical_item(url="https://x.example.com/b", text="A completely different story"),
        source_id=energy_source.id)
    assert outcome == "created"


def test_cross_feed_syndication_detected_from_fixtures(session, settings,
                                                       energy_feed, tech_feed):
    """The same wire story in two fixture feeds links as duplicate, not news."""
    SourceRepo(session).add(energy_feed.as_uri(), kind="rss")
    SourceRepo(session).add(tech_feed.as_uri(), kind="rss")
    job = run_collection(session, settings)
    totals = job.stats["totals"]
    # 3 energy items + 1 unique tech item + 1 syndicated copy of the grid story
    assert totals["created"] == 4
    assert totals["duplicate"] == 1

    dupes = [i for i in session.exec(select(Item)).all() if i.duplicate_of]
    assert len(dupes) == 1
    assert "grid-emergency" in dupes[0].duplicate_of or \
        "grid-emergency" in dupes[0].url_canonical
