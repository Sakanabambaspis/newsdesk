"""Shared fixtures: isolated settings/database per test, offline feed files,
and one factory for canonical records."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from newsdesk.config import Settings
from newsdesk.core.ids import canonical_url, content_hash, item_id_for, simhash64
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


def make_canonical_item(*, url: str = "https://x.example.com/a",
                        title: str = "Title", text: str = "Body text",
                        **overrides) -> dict:
    """Build one canonical record (DESIGN.md 3.2) for tests.

    ``id`` and the provenance hashes are derived from url/title/text unless
    overridden. A section kwarg (``source=``, ``content=``, ``timestamps=``,
    ``analysis=``, ``provenance=``) deep-merges over the defaults; any other
    kwarg replaces the top-level key.
    """
    now = datetime.now(timezone.utc).isoformat()
    item: dict = {
        "id": item_id_for(url),
        "source": {"publisher": "X", "url": url, "kind": "article", "author": None},
        "timestamps": {"published_at": now, "retrieved_at": now},
        "content": {"title": title, "text": text, "media": [], "transcript": None},
        "analysis": {"topics": [], "entities": [], "relevance": None, "claims": []},
        "provenance": {
            "content_hash": content_hash(title=title, text=text),
            "extraction_method": "rss",
            "url_canonical": canonical_url(url),
            "snapshot_path": None,
            "simhash": simhash64(f"{title}\n{text}"),
            "revision": 0,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(item.get(key), dict):
            item[key].update(value)
        else:
            item[key] = value
    return item
