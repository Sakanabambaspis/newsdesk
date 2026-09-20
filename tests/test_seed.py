"""Seed export/import: the versioned bridge for fresh databases (CI).

The property that matters: export -> import into an empty database ->
export again is identical, and importing twice adds nothing. Format 2
carries the workflow catalog (full history) and the stations, with
monotonic retirement.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import col, select
from typer.testing import CliRunner

from newsdesk.cli import app
from newsdesk.config import Settings
from newsdesk.core.models import LogEntry
from newsdesk.seed import FORMAT, SeedError, export_seed, import_seed
from newsdesk.storage.db import Database
from newsdesk.storage.repo import LogRepo, SourceRepo, WatchlistRepo
from newsdesk.workflow.catalog import WorkflowCatalog
from newsdesk.workflow.schema import FORMAT_VERSION
from newsdesk.workflow.stations import StationRepo

runner = CliRunner()


def _descriptor(name: str = "seeded-morning", version: int = 1) -> dict:
    return {
        "format_version": FORMAT_VERSION,
        "name": name,
        "version": version,
        "stages": [{"type": t} for t in ("collect", "select", "compose",
                                         "render", "publish", "notify")],
    }


def _log(session, action: str) -> list[LogEntry]:
    return list(session.exec(
        select(LogEntry).where(LogEntry.action == action)
        .order_by(col(LogEntry.id))))


@pytest.fixture
def populated(session):
    srepo, wrepo = SourceRepo(session), WatchlistRepo(session)
    s1, _ = srepo.add("https://a.example.com/feed.xml", title="A Desk",
                      publisher="A")
    s2, _ = srepo.add("https://b.example.com/feed.xml", title="B Desk",
                      fetch_interval_minutes=90)
    srepo.set_enabled(s2.id, False)
    wl = wrepo.create("morning", "the daily brief")
    wrepo.add_term(wl.id, "transformers", weight=1.5)
    wrepo.add_term(wl.id, "hype", kind="exclude")
    wrepo.attach_source(wl.id, s1.id)
    return srepo, wrepo


@pytest.fixture
def with_workflows(session, populated):
    """A catalog with a two-version workflow and a retired second name."""
    catalog = WorkflowCatalog(session)
    catalog.create_version(_descriptor("seeded-morning"), actor="user")
    catalog.create_version(_descriptor("seeded-morning", version=2),
                           actor="agent")
    catalog.create_version(_descriptor("old-morning"), actor="user")
    catalog.retire("old-morning", actor="user")
    return catalog


def _fresh_db(tmp_path: Path, name: str):
    settings = Settings(
        home=tmp_path / name,
        db_url=f"sqlite:///{(tmp_path / name / 'newsdesk.db').as_posix()}",
        min_request_interval=0.0,
    )
    settings.ensure_dirs()
    return Database(settings)


def test_export_import_roundtrip_is_lossless(session, populated, tmp_path):
    srepo, wrepo = populated
    first = export_seed(session)

    with _fresh_db(tmp_path, "home2").session() as fresh:
        stats = import_seed(fresh, first, actor="user")
        assert stats == {"sources_added": 2, "sources_present": 0,
                         "watchlists_created": 1, "terms_added": 2,
                         "terms_present": 0, "links_added": 1,
                         "links_present": 0,
                         "workflows_added": 0, "workflows_present": 0,
                         "stations_added": 0, "stations_updated": 0,
                         "stations_present": 0}
        assert export_seed(fresh) == first

        # the imported database is functionally identical
        fsrepo, fwrepo = SourceRepo(fresh), WatchlistRepo(fresh)
        sources = {s.url: s for s in fsrepo.list()}
        assert sources["https://b.example.com/feed.xml"].enabled is False
        assert sources["https://b.example.com/feed.xml"].fetch_interval_minutes == 90
        wl = next(w for w in fwrepo.list() if w.name == "morning")
        terms = {t.term: t for t in fwrepo.terms(wl.id)}
        assert terms["transformers"].weight == 1.5
        assert terms["hype"].kind == "exclude"
        assert wrepo.source_ids(wl.id) == \
            [sources["https://a.example.com/feed.xml"].id]


def test_reimport_is_idempotent(session, populated, tmp_path):
    data = export_seed(session)
    with _fresh_db(tmp_path, "home2").session() as fresh:
        first = import_seed(fresh, data, actor="user")
        second = import_seed(fresh, data, actor="user")
        assert first["sources_added"] == 2 and second["sources_added"] == 0
        assert second["sources_present"] == 2
        assert second["terms_added"] == 0 and second["terms_present"] == 2
        assert second["watchlists_created"] == 0
        assert second["links_added"] == 0 and second["links_present"] == 1
        assert export_seed(fresh) == data


def test_import_never_deletes(session, populated):
    data = export_seed(session)
    SourceRepo(session).add("https://c.example.com/feed.xml", title="C Desk")
    import_seed(session, data, actor="user")
    assert len(SourceRepo(session).list()) == 3


def test_import_requires_an_actor(session, populated):
    data = export_seed(session)
    with pytest.raises(TypeError):
        import_seed(session, data)  # must not silently default the actor


def test_import_rejects_wrong_format(session):
    with pytest.raises(SeedError):
        import_seed(session, {"format": 999}, actor="user")
    with pytest.raises(SeedError):
        import_seed(session, "not a dict", actor="user")
    # format 1 files predate the catalog sections; re-export instead
    with pytest.raises(SeedError, match="expected 2"):
        import_seed(session, {"format": 1, "sources": []}, actor="user")
    assert FORMAT == 2


def test_import_validates_entries(session):
    with pytest.raises(SeedError):
        import_seed(session, {"format": 2, "sources": [{"kind": "rss"}]},
                    actor="user")
    with pytest.raises(SeedError):
        import_seed(session, {"format": 2,
                              "sources": [{"url": "https://x.example.com/rss",
                                           "kind": "not-a-kind"}]},
                    actor="user")


# -- the workflows section (format 2) --------------------------------------------


def test_seed_format_2_carries_full_workflow_history(session, with_workflows):
    data = export_seed(session)
    assert data["format"] == 2
    assert [(d["name"], d["version"]) for d in data["workflows"]] == [
        ("old-morning", 1), ("seeded-morning", 1), ("seeded-morning", 2)]
    assert data["retired_workflows"] == ["old-morning"]


def test_workflow_section_roundtrips_history_and_retirement(
        session, with_workflows, tmp_path):
    data = export_seed(session)
    with _fresh_db(tmp_path, "home2").session() as fresh:
        stats = import_seed(fresh, data, actor="user")
        assert stats["workflows_added"] == 3 and stats["workflows_present"] == 0
        fresh_catalog = WorkflowCatalog(fresh)
        # versions + documents round-trip; authorship is the import's
        # (the file carries documents, not provenance — the log's
        # via=seed tells the origin story)
        identity = lambda versions: [(ver["version"], ver["document"])
                                     for ver in versions]
        assert identity(fresh_catalog.versions("seeded-morning")) == \
            identity(with_workflows.versions("seeded-morning"))
        # a fresh DB can serve a pinned old version and refuses the retired
        assert fresh_catalog.resolve("seeded-morning@1")["version"] == 1
        with pytest.raises(Exception, match="retired"):
            fresh_catalog.resolve("old-morning")
        assert export_seed(fresh) == data


def test_workflow_import_is_idempotent(session, with_workflows):
    data = export_seed(session)
    second = import_seed(session, data, actor="user")
    assert second["workflows_added"] == 0
    assert second["workflows_present"] == 3


def test_conflicting_workflow_version_is_loud(session, with_workflows):
    data = export_seed(session)
    clashing = _descriptor("seeded-morning", version=1)
    clashing["title"] = "a rewrite of immutable history"
    data["workflows"][1] = clashing
    with pytest.raises(SeedError, match="history was violated"):
        import_seed(session, data, actor="user")


def test_stale_seed_never_unretires(session, with_workflows):
    """A seed exported before a name was retired carries no retire
    instruction; importing it after a local un-retire must not re-retire
    (a seed can retire, never un-retire — and its silence retires
    nothing)."""
    stale = export_seed(session)
    stale["retired_workflows"] = []  # an older file, pre-retirement
    WorkflowCatalog(session).unretire("old-morning", actor="user")
    import_seed(session, stale, actor="user")
    entry = next(e for e in WorkflowCatalog(session).list()
                 if e["name"] == "old-morning")
    assert entry["retired_at"] is None  # the deliberate local action stands


def test_retirement_applies_monotonically(session, with_workflows, tmp_path):
    data = export_seed(session)
    with _fresh_db(tmp_path, "home2").session() as fresh:
        import_seed(fresh, data, actor="user")
        # re-import the same seed: retiring an already-retired name is calm
        import_seed(fresh, data, actor="user")
        assert WorkflowCatalog(fresh).resolve("seeded-morning")


def test_seed_rejects_invalid_workflow_documents(session):
    bad = _descriptor()
    bad["stages"][0]["type"] = "transmogrify"
    with pytest.raises(SeedError, match="transmogrify"):
        import_seed(session, {"format": 2, "workflows": [bad]}, actor="user")


def test_seed_rejects_version_gaps(session):
    with pytest.raises(SeedError, match="must be 1"):
        import_seed(session, {"format": 2,
                              "workflows": [_descriptor(version=2)]},
                    actor="user")


def test_workflow_actors_and_via_are_recorded(session, with_workflows,
                                              tmp_path):
    data = export_seed(session)
    with _fresh_db(tmp_path, "home2").session() as fresh:
        import_seed(fresh, data, actor="user")
        versioned = _log(fresh, "workflow_version_created")
        assert len(versioned) == 3
        assert all(e.actor == "user" for e in versioned)
        assert all(e.detail.get("via") == "seed" for e in versioned)


# -- the seed CLI -----------------------------------------------------------------


def test_seed_cli_roundtrip_between_homes(tmp_path, monkeypatch):
    home1, home2 = tmp_path / "home1", tmp_path / "home2"
    monkeypatch.setenv("NEWSDESK_HOME", str(home1))
    result = runner.invoke(app, ["add-source", "https://a.example.com/feed.xml",
                                 "--title", "A"])
    assert result.exit_code == 0, result.output
    settings = Settings.from_env()
    settings.ensure_dirs()
    with Database(settings).session() as s:
        wl = WatchlistRepo(s).create("morning")
        WatchlistRepo(s).add_term(wl.id, "transformers")

    out = tmp_path / "seed" / "out.json"
    result = runner.invoke(app, ["seed", "export", str(out)])
    assert result.exit_code == 0, result.output
    assert "1 sources, 1 terms" in result.output

    monkeypatch.setenv("NEWSDESK_HOME", str(home2))
    result = runner.invoke(app, ["seed", "import", str(out)])
    assert result.exit_code == 0, result.output
    assert "+1 sources" in result.output
    # idempotent in the same database
    result = runner.invoke(app, ["seed", "import", str(out)])
    assert result.exit_code == 0, result.output
    assert "+0 sources" in result.output


def test_seed_cli_import_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSDESK_HOME", str(tmp_path / "home"))
    result = runner.invoke(app, ["seed", "import", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
    assert "no such seed file" in result.output


def test_seed_cli_imports_workflow_versions(tmp_path, monkeypatch):
    """The CLI import records workflow versions as actor=user, via=seed."""
    monkeypatch.setenv("NEWSDESK_HOME", str(tmp_path / "home1"))
    with _fresh_db(tmp_path, "home1").session() as s:
        WorkflowCatalog(s).create_version(_descriptor(), actor="agent")

    out = tmp_path / "seed" / "out.json"
    result = runner.invoke(app, ["seed", "export", str(out)])
    assert result.exit_code == 0, result.output

    monkeypatch.setenv("NEWSDESK_HOME", str(tmp_path / "home2"))
    result = runner.invoke(app, ["seed", "import", str(out)])
    assert result.exit_code == 0, result.output
    assert "+1 workflow versions" in result.output
    with _fresh_db(tmp_path, "home2").session() as s:
        catalog = WorkflowCatalog(s)
        assert catalog.resolve("seeded-morning")["name"] == "seeded-morning"
        versioned = [e for e in LogRepo(s).recent(50)
                     if e.action == "workflow_version_created"]
        assert versioned[0].actor == "user"  # the CLI is the user's surface
        assert versioned[0].detail["via"] == "seed"


# -- the stations section (W4, ticket 12) -----------------------------------------


def test_stations_roundtrip_immutably(session, populated, tmp_path):
    """Stations ride the seed with name-keyed documents: export -> import ->
    export is identical, metadata edits win on re-import (like sources), a
    changed path_segment is loud, and retirement is monotonic."""
    Stations = StationRepo(session)
    Stations.create("briefing", actor="user", watchlist_id=1,
                    workflow_ref="default-morning")
    Stations.create("papers", actor="user", watchlist_id=1,
                    path_segment="papers", feed_title="Paper Trail",
                    feed_author="Paper Trail")
    seeded = export_seed(session)
    assert [s["name"] for s in seeded["stations"]] == ["briefing", "papers"]
    papers = seeded["stations"][1]
    assert papers["watchlist"] == "morning"  # name, not id
    assert papers["feed"] == {"title": "Paper Trail", "author": "Paper Trail"}
    assert papers["path_segment"] == "papers"

    with _fresh_db(tmp_path, "home2").session() as fresh:
        stats = import_seed(fresh, seeded, actor="user")
        assert stats["stations_added"] == 2
        assert export_seed(fresh) == seeded
        assert import_seed(fresh, seeded, actor="user")["stations_present"] == 2

        stolen = json.loads(json.dumps(seeded))
        stolen["stations"][1]["path_segment"] = "papers-2"
        with pytest.raises(SeedError, match="immutable"):
            import_seed(fresh, stolen, actor="user")

        edited = json.loads(json.dumps(seeded))
        edited["stations"][1]["workflow"] = "seeded-morning"
        edited["stations"][1]["feed"]["title"] = "Paper Trail v2"
        stats = import_seed(fresh, edited, actor="user")
        assert stats["stations_updated"] == 1
        row = StationRepo(fresh).get("papers")
        assert row["workflow_ref"] == "seeded-morning"
        assert row["feed_title"] == "Paper Trail v2"

        # a retired station stays retired: a stale seed never un-retires
        StationRepo(fresh).retire("papers", actor="user")
        assert import_seed(fresh, seeded, actor="user")["stations_present"] == 1
        assert StationRepo(fresh).get("papers")["retired_at"] is not None

        # and a seed that says retired: true retires on import
        retired_seed = json.loads(json.dumps(seeded))
        retired_seed["stations"][1]["retired"] = True
        import_seed(fresh, retired_seed, actor="user")
        assert StationRepo(fresh).get("papers")["retired_at"] is not None
        assert [s["name"] for s in StationRepo(fresh).list()] == \
            ["briefing", "papers"]  # never deleted


def test_seed_rejects_bad_station_documents(session, populated):
    base = {"format": FORMAT, "stations": [{"name": "papers"}]}
    with pytest.raises(SeedError, match="unknown watchlist 'ghost'"):
        import_seed(session, {**base, "stations": [
            {"name": "papers", "watchlist": "ghost"}]}, actor="user")
    with pytest.raises(SeedError, match="unknown keys"):
        import_seed(session, {**base, "stations": [
            {"name": "papers", "feed": {"titel": "typo"}}]}, actor="user")
    with pytest.raises(SeedError, match="path_segment must match"):
        import_seed(session, {**base, "stations": [
            {"name": "papers", "path_segment": "Not A Slug"}]}, actor="user")
    # a pre-W4 format-2 file simply has no stations section: calm import
    assert import_seed(session, {"format": FORMAT}, actor="user")[
        "stations_added"] == 0


def test_committed_seed_file_defines_the_stations(session):
    """Ticket 12 acceptance: >=2 stations ride the committed seed — the one
    the CI legs import before their run."""
    committed = json.loads(
        (Path(__file__).parents[1] / "seed" / "newsdesk-seed.json")
        .read_text(encoding="utf-8"))
    stats = import_seed(session, committed, actor="user")
    assert stats["stations_added"] >= 2
    by_name = {s["name"]: s for s in StationRepo(session).list()}
    # the default station is the legacy root feed; the other sits at its own
    # permanent path segment and carries its own identity
    assert by_name["morning-briefing"]["path_segment"] is None
    assert by_name["papers"]["path_segment"] == "papers"
    assert by_name["papers"]["feed_title"] == "Paper Trail"
    # every station's watchlist binding resolved (never a dangling name)
    assert all(s["watchlist_id"] is not None for s in by_name.values())
    assert all(s["workflow_ref"] for s in by_name.values())
