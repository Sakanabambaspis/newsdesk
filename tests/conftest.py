"""Shared fixtures: isolated settings/database per test, offline feed files."""

from __future__ import annotations

from pathlib import Path

import pytest

from newsdesk.config import Settings
from newsdesk.core.models import Source
from newsdesk.storage.db import Database

FEEDS = Path(__file__).parent / "fixtures" / "feeds"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    home = tmp_path / "newsdesk-home"
    s = Settings(
        home=home,
        db_url=f"sqlite:///{(home / 'newsdesk.db').as_posix()}",
        min_request_interval=0.0,
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def db(settings: Settings) -> Database:
    return Database(settings)


@pytest.fixture
def session(db: Database):
    with db.session() as s:
        yield s


@pytest.fixture
def energy_feed() -> Path:
    return FEEDS / "sample-energy.xml"


@pytest.fixture
def tech_feed() -> Path:
    return FEEDS / "sample-tech.xml"


@pytest.fixture
def energy_source(session, energy_feed: Path) -> Source:
    from newsdesk.storage.repo import SourceRepo

    source, _ = SourceRepo(session).add(energy_feed.as_uri(), kind="rss",
                                        title="Example Energy Desk")
    return source
