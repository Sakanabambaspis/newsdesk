"""Seed export/import: the versioned bridge for fresh databases (CI).

The property that matters: export -> import into an empty database ->
export again is identical, and importing twice adds nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsdesk.cli import app
from newsdesk.config import Settings
from newsdesk.seed import SeedError, export_seed, import_seed
from newsdesk.storage.db import Database
from newsdesk.storage.repo import SourceRepo, WatchlistRepo

runner = CliRunner()


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
        stats = import_seed(fresh, first)
        assert stats == {"sources_added": 2, "sources_present": 0,
                         "watchlists_created": 1, "terms_added": 2,
                         "terms_present": 0, "links_added": 1,
                         "links_present": 0}
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
        first = import_seed(fresh, data)
        second = import_seed(fresh, data)
        assert first["sources_added"] == 2 and second["sources_added"] == 0
        assert second["sources_present"] == 2
        assert second["terms_added"] == 0 and second["terms_present"] == 2
        assert second["watchlists_created"] == 0
        assert second["links_added"] == 0 and second["links_present"] == 1
        assert export_seed(fresh) == data


def test_import_never_deletes(session, populated):
    data = export_seed(session)
    SourceRepo(session).add("https://c.example.com/feed.xml", title="C Desk")
    import_seed(session, data)
    assert len(SourceRepo(session).list()) == 3


def test_import_rejects_wrong_format(session):
    with pytest.raises(SeedError):
        import_seed(session, {"format": 999})
    with pytest.raises(SeedError):
        import_seed(session, "not a dict")


def test_import_validates_entries(session):
    with pytest.raises(SeedError):
        import_seed(session, {"format": 1, "sources": [{"kind": "rss"}]})
    with pytest.raises(SeedError):
        import_seed(session, {"format": 1,
                              "sources": [{"url": "https://x.example.com/rss",
                                           "kind": "not-a-kind"}]})


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
