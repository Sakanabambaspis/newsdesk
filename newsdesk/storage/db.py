"""Database bootstrap: engine creation, schema, and the FTS5 full-text index.

SQLite with FTS5 is the zero-setup local dev/default target. The same repos
run unchanged against Postgres by swapping ``Settings.db_url``; FTS has a
LIKE-based fallback when the module is unavailable.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from ..config import Settings

_FTS_STATEMENTS = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5("
    "item_id UNINDEXED, title, text, publisher, tokenize='porter unicode61')",
    "CREATE TRIGGER IF NOT EXISTS items_fts_ai AFTER INSERT ON items BEGIN "
    "INSERT INTO items_fts(item_id, title, text, publisher) "
    "VALUES (new.id, new.title, new.text, new.publisher); END",
    "CREATE TRIGGER IF NOT EXISTS items_fts_ad AFTER DELETE ON items BEGIN "
    "DELETE FROM items_fts WHERE item_id = old.id; END",
    "CREATE TRIGGER IF NOT EXISTS items_fts_au AFTER UPDATE ON items BEGIN "
    "DELETE FROM items_fts WHERE item_id = old.id; "
    "INSERT INTO items_fts(item_id, title, text, publisher) "
    "VALUES (new.id, new.title, new.text, new.publisher); END",
]


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()
        self.engine: Engine = create_engine(settings.database_url, echo=False)
        if self.engine.url.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", _sqlite_on_connect)
        # Import models so they register on the metadata before create_all.
        from .. import core  # noqa: F401
        SQLModel.metadata.create_all(self.engine)
        self.fts_enabled = self._init_fts()

    def _init_fts(self) -> bool:
        try:
            with self.engine.begin() as conn:
                for stmt in _FTS_STATEMENTS:
                    conn.exec_driver_sql(stmt)
            return True
        except Exception:
            return False  # search falls back to LIKE; see storage.fts

    def session(self) -> Session:
        return Session(self.engine)


def _sqlite_on_connect(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
