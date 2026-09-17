"""FTS5 query building. Returns None when FTS is unavailable so callers can
fall back to a LIKE search (storage.repo.ItemRepo.search)."""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlmodel import Session

# Scripts without whitespace word boundaries (CJK, Thai, Lao): the unicode61
# tokenizer indexes each run as one long token, so a short query can never
# match a longer indexed run and only substring search can find it.
_UNSEGMENTED_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\uac00-\ud7af\u0e00-\u0e7f]")


def has_unsegmented_script(query: str) -> bool:
    return bool(_UNSEGMENTED_RE.search(query))


def _quote(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def search_ids(session: Session, query: str, limit: int = 50) -> list[str] | None:
    """Return item IDs matching the query, best rank first.

    Tries the whole query as a phrase first; if that is empty and the query has
    multiple words, retries with OR across tokens. Raises nothing: on FTS
    unavailability or a syntax error, returns None so the caller can degrade.
    """
    phrase = _quote(query.strip())
    sql = text(
        "SELECT item_id FROM items_fts WHERE items_fts MATCH :q "
        "ORDER BY rank LIMIT :limit"
    )
    try:
        rows = session.execute(sql, {"q": phrase, "limit": limit}).all()
        if rows:
            return [r[0] for r in rows]
        tokens = [t for t in query.split() if t]
        if len(tokens) > 1:
            rows = session.execute(
                sql, {"q": " OR ".join(_quote(t) for t in tokens), "limit": limit}
            ).all()
            return [r[0] for r in rows]
        return []
    except Exception:
        return None
