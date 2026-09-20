"""W4 stations: the repo semantics, the station run, and idempotency
(wayfinder tickets 10 + 12).

Ticket 12's acceptance, pinned: two stations publish independent feeds
(own path, own GUIDs, own identity) and one station's deploy never
disturbs another's archive; a same-date re-run no-ops only that station;
the digest ranks by the station's scoped watchlist (terms + attached
sources), not the global union; unknown or retired stations fail loudly
before any work.

Fully offline: file:// fixture feed, keyless LLM (the extractive writer),
a stubbed edge-tts save, the local-dir publisher.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from newsdesk.storage.repo import ItemRepo, SourceRepo, WatchlistRepo
from newsdesk.workflow.catalog import CatalogError
from newsdesk.workflow.engine import WorkflowRunError, load_descriptor, \
    run_workflow
from newsdesk.workflow.stations import StationRepo, station_scope
from tests.conftest import FEEDS, make_canonical_item

TOKEN = "3f9a1c77b2d84e05a1c3f6d2b9e08417"
DATE = "2026-09-20"
_FRAME = b"\xff\xf3\x64\x00" + b"\x00" * 140  # one edge-tts MP3 frame


def _fake_edge_save(text, voice, path):
    path.write_bytes(_FRAME * (40 + len(text)))


def _mk_item(session, source, n: int, *, title: str, term: str,
             publisher: str = "Alpha Wire") -> str:
    """One item, relevance left to term matching (``analysis.relevance``
    None), so the scope's include terms decide the ranking."""
    when = datetime.now(timezone.utc) - timedelta(hours=1 + n / 10)
    item = make_canonical_item(
        url=f"https://{publisher.lower().replace(' ', '')}.example/{n}",
        title=title, text=f"{term} did a novel thing. " * 4,
        id=f"item_statest{n:014d}",
        source={"publisher": publisher, "url": f"https://x.example/{n}",
                "kind": "article", "author": None},
        timestamps={"published_at": when.isoformat(),
                    "retrieved_at": when.isoformat()},
        provenance={"content_hash": f"h{n}"},
    )
    outcome, row = ItemRepo(session).upsert(item, source_id=source.id)
    assert outcome == "created"
    return row.id


@pytest.fixture
def offline(settings, monkeypatch):
    settings.feed_token = TOKEN
    settings.tts_pace = 0.0
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    return settings


def _two_source_world(session) -> dict[str, str]:
    """Two sources, two watchlists: the global union matches everything,
    the papers watchlist only the tech source's items."""
    energy, _ = SourceRepo(session).add(
        (FEEDS / "sample-energy.xml").as_uri(), kind="rss")
    tech, _ = SourceRepo(session).add(
        (FEEDS / "sample-tech.xml").as_uri(), kind="rss")
    wrepo = WatchlistRepo(session)
    briefing = wrepo.create("briefing", "the daily brief")
    wrepo.add_term(briefing.id, "grid")
    papers = wrepo.create("papers", "research papers only")
    wrepo.add_term(papers.id, "diffusion")
    wrepo.add_term(papers.id, "benchmark")
    wrepo.attach_source(papers.id, tech.id)
    return {
        "energy_story": _mk_item(session, energy, 1,
                                 title="Grid storage costs fall", term="grid"),
        "paper_a": _mk_item(session, tech, 2,
                            title="Diffusion model scales", term="diffusion"),
        "paper_b": _mk_item(session, tech, 3,
                            title="Benchmark suite released",
                            term="benchmark"),
    }


def _stations(session) -> StationRepo:
    """The two seeded-shape stations: the default (legacy root, the global
    watchlist) and a scoped papers station under its own path segment."""
    repo = StationRepo(session)
    repo.create("morning-briefing", actor="user",
                watchlist_id=1, workflow_ref="default-morning")
    repo.create("papers", actor="user", watchlist_id=2,
                path_segment="papers", feed_title="Paper Trail",
                feed_description="A short daily research-papers briefing.",
                feed_author="Paper Trail")
    return repo


def _run(session, settings, station=None, *, dry_run=False):
    return run_workflow(session, settings, load_descriptor("default-morning", 1),
                        station, date=DATE, dry_run=dry_run)


def _script(settings, station=None) -> dict:
    base = settings.morning_dir / station / DATE if station \
        else settings.morning_dir / DATE
    return json.loads((base / f"{DATE}-script.json").read_text())


def _spoken(settings, station=None) -> list[str]:
    return [iid for s in _script(settings, station)["sections"]
            for iid in s["item_ids"]]


def _manifest(settings, segment=None) -> list[dict]:
    root = (settings.publish_dir / TOKEN).resolve()
    return json.loads(((root / segment) if segment else root)
                      .joinpath("episodes.json").read_text())


def _feed(settings, segment=None) -> str:
    root = (settings.publish_dir / TOKEN).resolve()
    return ((root / segment) if segment else root).joinpath("feed.xml").read_text()


# -- the repo semantics ----------------------------------------------------------


def test_station_repo_is_retire_never_delete_with_a_required_actor(session):
    _two_source_world(session)
    repo = _stations(session)
    with pytest.raises(CatalogError, match="actor must be one of"):
        repo.create("rogue", actor="nobody")
    with pytest.raises(CatalogError, match="already exists"):
        repo.create("papers", actor="user")
    with pytest.raises(CatalogError, match="reserved"):
        repo.create("audio", actor="user", path_segment="audio")
    with pytest.raises(CatalogError, match="watchlist 404 does not exist"):
        repo.create("stale", actor="user", watchlist_id=404)
    # name and path_segment are not editable at all: no rename, no path edit
    repo.update_identity("papers", actor="user", workflow_ref="default-morning",
                         watchlist_id=2, feed_title="Paper Trail v2")
    assert repo.get("papers")["name"] == "papers"
    assert repo.get("papers")["path_segment"] == "papers"
    assert repo.get("papers")["feed_title"] == "Paper Trail v2"
    # retire refuses runs and never deletes the row (the archive stays live)
    repo.retire("papers", actor="user")
    with pytest.raises(CatalogError, match="retired"):
        repo.resolve("papers")
    assert repo.get("papers")["retired_at"] is not None
    assert [s["name"] for s in repo.list()] == ["morning-briefing", "papers"]
    repo.unretire("papers", actor="user")
    assert repo.resolve("papers")["path_segment"] == "papers"
    with pytest.raises(CatalogError, match="unknown station"):
        repo.resolve("ghost")


def test_station_scope_is_the_bound_watchlist_not_the_global_union(session):
    _two_source_world(session)
    global_scope = station_scope(session, None)
    assert global_scope["exclude_terms"] == []
    assert global_scope["source_ids"] is None
    assert {t for t, _w in global_scope["include_terms"]} == \
        {"grid", "diffusion", "benchmark"}
    scoped = station_scope(session, 2)
    assert scoped["include_terms"] == [("diffusion", 1.0), ("benchmark", 1.0)]
    assert scoped["source_ids"] == [2]  # only the tech source is attached


# -- the station run -------------------------------------------------------------


def test_two_stations_publish_independent_feeds_and_neither_disturbs_the_other(
        session, offline):
    world = _two_source_world(session)
    _stations(session)

    default_report = _run(session, offline, "morning-briefing")
    assert default_report["outcome"] == "published"
    assert default_report["station"] == "morning-briefing"
    root_feed = _feed(offline)
    assert "<title>Morning Briefing</title>" in root_feed
    assert _manifest(offline)[0]["guid"] == f"morning-briefing-{DATE}"

    papers_report = _run(session, offline, "papers")
    assert papers_report["outcome"] == "published"
    papers_feed = _feed(offline, "papers")
    assert "<title>Paper Trail</title>" in papers_feed
    assert f"{TOKEN}/papers/audio/{DATE}.mp3" in papers_feed
    papers_manifest = _manifest(offline, "papers")
    assert [e["guid"] for e in papers_manifest] == [f"papers-{DATE}"]
    assert (offline.publish_dir / TOKEN / "papers" / "audio" /
            f"{DATE}.mp3").exists()
    # the papers deploy left the legacy root's feed and manifest untouched
    assert _feed(offline) == root_feed
    assert [e["guid"] for e in _manifest(offline)] == \
        [f"morning-briefing-{DATE}"]
    # and the two stations' episodes are different episodes
    assert set(_spoken(offline, "morning-briefing")) != set(_spoken(offline, "papers"))
    assert world["energy_story"] in _spoken(offline, "morning-briefing")
    assert world["energy_story"] not in _spoken(offline, "papers")


def test_station_run_ranks_by_its_scoped_watchlist(session, offline):
    """The papers station sees only its own watchlist's terms and only the
    source attached to it — never the global union."""
    world = _two_source_world(session)
    _stations(session)
    _run(session, offline, "papers")
    spoken = set(_spoken(offline, "papers"))
    assert spoken == {world["paper_a"], world["paper_b"]}
    assert world["energy_story"] not in spoken
    # the station's sidecar lands under its own (station, date) layout
    assert (offline.morning_dir / "papers" / DATE /
            f"{DATE}-script.json").exists()


def test_same_date_rerun_noops_only_the_station_that_already_published(
        session, offline):
    _two_source_world(session)
    _stations(session)
    _run(session, offline, "papers")
    papers_manifest = _manifest(offline, "papers")

    rerun = _run(session, offline, "papers")
    assert rerun == {"date": DATE, "outcome": "already_published",
                     "stages": {}, "station": "papers"}
    assert _manifest(offline, "papers") == papers_manifest
    # the sibling station has published nothing yet: its run still does work
    assert _run(session, offline, "morning-briefing")["outcome"] == "published"
    assert _run(session, offline, "morning-briefing")["outcome"] == \
        "already_published"


def test_unknown_or_retired_station_fails_loudly_before_any_work(session, offline):
    _two_source_world(session)
    repo = _stations(session)
    with pytest.raises(WorkflowRunError, match="selection stage failed.*"
                                               "unknown station 'ghost'"):
        _run(session, offline, "ghost")
    repo.retire("papers", actor="user")
    with pytest.raises(WorkflowRunError, match="selection stage failed.*retired"):
        _run(session, offline, "papers")
    assert not (offline.publish_dir / TOKEN / "papers" / "episodes.json").exists()


def test_station_less_run_never_reads_the_stations_table(session, offline):
    """``station=None`` is the legacy path: identical output with or
    without stations defined (the pre-W4 behavior survives)."""
    _two_source_world(session)
    before = _run(session, offline, dry_run=True)["stages"]["digest"]
    _stations(session)
    after = _run(session, offline, dry_run=True)["stages"]["digest"]
    assert after == before
