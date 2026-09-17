"""Raw-payload snapshots: the one implementation of the DESIGN.md 5.3 policy.

Every fetcher persists the bytes the source actually served, under
``$NEWSDESK_HOME/snapshots/src{id}_{stamp}_{hash8}.{ext}``. Snapshotting is
best-effort: an ``OSError`` returns ``None`` and the item is still stored,
with ``snapshot_path = null`` marking the gap.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..core.ids import sha256_hex


def save_snapshot(settings, *, source_id: int | None, payload: bytes, ext: str,
                  when: datetime | None = None) -> str | None:
    """Write one snapshot file; returns its path, or None on failure."""
    digest = sha256_hex(payload)
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S")
    path = _snapshots_dir(settings) / f"src{source_id or 0}_{stamp}_{digest[:8]}.{ext}"
    try:
        path.write_bytes(payload)
    except OSError:
        return None
    return str(path)


def _snapshots_dir(settings) -> Path:
    settings.snapshots_dir.mkdir(parents=True, exist_ok=True)
    return settings.snapshots_dir
