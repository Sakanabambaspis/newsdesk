"""Synthetic feed fixtures through fetch -> normalize -> dedupe -> storage.

All fixtures are deterministic, offline (file:// URIs via the local-fetch
path), and new in this batch — see tests/fixtures/feeds/synthetic-*.xml.
Known quirks the fixtures pin are cross-referenced to
docs/reviews/fixture-findings-20260917.md (F-1, F-2, F-3); the code is
characterized here, never modified.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import col, select

from newsdesk.core.ids import item_id_for
from newsdesk.core.models import Item
from newsdesk.pipeline.runner import run_collection
from newsdesk.storage.repo import ItemRepo, SourceRepo

FEEDS = Path(__file__).parent / "fixtures" / "feeds"


def _add(session, name: str) -> int:
    source, _ = SourceRepo(session).add((FEEDS / name).as_uri(), kind="rss",
                                        title=name)
    return source.id


def test_rss_fixture_roundtrip(session, settings):
    _add(session, "synthetic-rss.xml")
    job = run_collection(session, settings)
    assert job.stats["totals"] == {"created": 3, "updated": 0, "unchanged": 0,
                                   "duplicate": 0}
    rows = ItemRepo(session).recent(limit=10)
    titles = {r.title for r in rows}
    assert "Regional power auction clears at record price" in titles
    assert "Transit agency expands overnight service" in titles


def test_atom_syndication_duplicates_across_feeds(session, settings):
    """The Atom Tribune verbatim copy of the Meridian Wire story must land as
    a duplicate linked to the original — syndication, not a second event."""
    _add(session, "synthetic-rss.xml")
    _add(session, "synthetic-atom.xml")
    job = run_collection(session, settings)
    totals = job.stats["totals"]
    assert totals["created"] == 4  # 3 wire items + the unique ferry story
    assert totals["duplicate"] == 1

    rows = list(session.exec(
        select(Item).where(Item.duplicate_of != None)  # noqa: E712
    ))
    assert len(rows) == 1
    dup_row = rows[0]
    assert dup_row.url == "https://atom-tribune.example/wire/power-auction"
    original = ItemRepo(session).get(dup_row.duplicate_of)
    assert original.url == "https://meridian-wire.example/stories/power-auction"
    assert dup_row.content_hash == original.content_hash
    assert dup_row.publisher == "Atom Tribune"  # copy attributed to the outlet that served it; duplicate_of links to the wire original


def test_second_pass_is_fully_idempotent(session, settings):
    for name in ("synthetic-rss.xml", "synthetic-atom.xml", "synthetic-podcast.xml",
                 "synthetic-video-feed.xml", "synthetic-i18n.xml",
                 "synthetic-entities.xml", "synthetic-bare.xml"):
        _add(session, name)
    run_collection(session, settings)
    job2 = run_collection(session, settings)
    totals = job2.stats["totals"]
    assert totals["created"] == 0 and totals["updated"] == 0
    assert totals["duplicate"] == 0
    assert totals["unchanged"] == 16  # every stored row (bare entries carry no content and are skipped at normalize)


def test_item_ids_deterministic_across_runs(session, settings):
    _add(session, "synthetic-rss.xml")
    run_collection(session, settings)
    first = {r.url: r.id for r in ItemRepo(session).recent(limit=10)}
    # re-collect into a fresh DB with the same fixture: ids match bit-for-bit
    import tempfile
    from newsdesk.config import Settings as S
    from newsdesk.storage.db import Database
    tmp = Path(tempfile.mkdtemp())
    s2 = S(home=tmp, db_url=f"sqlite:///{(tmp/'x.db').as_posix()}",
           min_request_interval=0.0)
    s2.ensure_dirs()
    with Database(s2).session() as sess2:
        _add(sess2, "synthetic-rss.xml")
        run_collection(sess2, s2)
        second = {r.url: r.id for r in sess2.exec(select(Item)).all()}
    assert first == second
    assert first["https://meridian-wire.example/stories/power-auction"] == \
        item_id_for("https://meridian-wire.example/stories/power-auction")


def test_podcast_enclosures_become_media(session, settings):
    _add(session, "synthetic-podcast.xml")
    run_collection(session, settings)
    rows = {r.url: r for r in ItemRepo(session).recent(limit=10)}
    ep41 = rows["https://signalpath.example/episodes/41"]
    assert ep41.media == [{"url": "https://cdn.signalpath.example/audio/ep41.mp3",
                           "type": "audio/mpeg", "length": "52428800"}]
    # CDATA show notes: paragraphs extracted, tracking script dropped
    assert "idempotency key pattern" in ep41.text
    assert "tracking_beacon" not in ep41.text
    ep42 = rows["https://signalpath.example/episodes/42"]
    assert ep42.media[0]["length"] == "48168960"
    assert "three public postmortems" in ep42.text


def test_video_feed_media_content_mapping(session, settings):
    """media:content entries (the shape YouTube channel feeds use) map into
    content.media[] with url/type preserved."""
    _add(session, "synthetic-video-feed.xml")
    run_collection(session, settings)
    rows = {r.url: r for r in ItemRepo(session).recent(limit=10)}
    v1 = rows["https://videos.example.com/watch?v=fn0042"]
    assert v1.media[0]["url"] == "https://videos.example.com/embed/fn0042"
    assert v1.media[0]["type"] == "application/x-shockwave-flash"
    assert "substation upgrade" in v1.title.lower()
    # item 2 has a thumbnail but no description -> text stays empty
    v2 = rows["https://videos.example.com/watch?v=fn0043"]
    assert v2.text == "" and len(v2.media) == 1


def test_i18n_content_roundtrip_and_fts(session, settings):
    """UTF-8 titles/text survive the whole pipeline; Latin-script i18n is
    searchable via FTS; CJK substring search falls back to LIKE (F-3 fixed)."""
    _add(session, "synthetic-i18n.xml")
    run_collection(session, settings)
    rows = {r.url: r for r in ItemRepo(session).recent(limit=10)}
    jp = rows["https://i18n-news.example/jp/2026/09/energieshift"]
    assert jp.title.startswith("再生可能エネルギー")
    assert "過去最高" in jp.text
    ar = rows["https://i18n-news.example/ar/2026/09/solar-pilot"]
    assert ar.title.startswith("مشروع للطاقة")
    de = rows["https://i18n-news.example/de/2026/09/wasserstoffnetz"]
    assert "Wasserstoffnetz" in de.title

    repo = ItemRepo(session)
    # whitespace-delimited scripts: phrase search works
    assert [i.id for i in repo.search("Wasserstoffnetz")] == [de.id]
    assert [i.id for i in repo.search("الطاقة الشمسية")] == [ar.id]
    # F-3 (fixed): unicode61 cannot segment CJK, so an empty FTS result for a
    # CJK query falls back to substring matching instead of a silent miss.
    assert [i.id for i in repo.search("再生可能")] == [jp.id]
    assert [i.id for i in repo.search("エネルギー")] == [jp.id]


def test_html_entities_and_cdata_edge_cases(session, settings):
    _add(session, "synthetic-entities.xml")
    run_collection(session, settings)
    rows = {r.url: r for r in ItemRepo(session).recent(limit=10)}

    # &amp;amp; in the feed -> &amp; in HTML -> & after strip: decoded once per layer
    de = rows["https://edgecase.example/2026/double-escape"]
    assert "AT&T costs rose" in de.text
    assert "— a first for the region <this year>." in de.text

    # CDATA body: entities decode at the HTML layer, script/style vanish
    cd = rows["https://edgecase.example/2026/cdata-body"]
    assert "3 percent & the board unanimously approved the freeze." in cd.text
    assert "pixel_tracker" not in cd.text
    assert "display: none" not in cd.text
    assert cd.text.endswith("Credits expire in June.")

    # numeric character references decode in titles
    nr = rows["https://edgecase.example/2026/numeric-refs"]
    assert nr.title == "Bulletin — three items approved • unanimous"

    # quoted attrs: query params survive and canonicalize deterministically
    q = rows["https://edgecase.example/2026/quoted-attrs?edition=morning&region=west"]
    assert q.url_canonical == \
        "https://edgecase.example/2026/quoted-attrs?edition=morning&region=west"


def test_bare_items_are_skipped_not_merged(session, settings):
    """F-1 (fixed): entries with no title, text, or transcript carry nothing
    to anchor a claim or a content hash to, so normalization skips them
    instead of letting them collide into one duplicate equivalence class."""
    _add(session, "synthetic-bare.xml")
    job = run_collection(session, settings)
    assert job.stats["totals"]["created"] == 0
    assert job.stats["totals"]["duplicate"] == 0

    rows = list(session.exec(
        select(Item).where(col(Item.url).startswith("https://bare.example/2026/"))))
    assert rows == []  # nothing stored, nothing silently merged


def test_relative_link_resolves_against_publisher_site(session, settings):
    """F-2 (fixed): a site-relative <link> in a locally-imported feed
    resolves against the publisher's web base (the channel <link>), not the
    reader's filesystem."""
    _add(session, "synthetic-rss.xml")
    run_collection(session, settings)
    rows = [r for r in ItemRepo(session).recent(limit=10)
            if r.title and "relative link" in r.title]
    assert len(rows) == 1
    row = rows[0]
    assert row.url == "https://meridian-wire.example/stories/relative-path"
    assert row.url_canonical == row.url
