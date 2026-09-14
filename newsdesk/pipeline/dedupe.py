"""Deduplication tiers (DESIGN.md section 7).

Tier 1 (exact) is implemented today and lives in ItemRepo.upsert:
URL-identity + content-hash revision detection + cross-URL syndication
linking. Tiers 2-3 (simhash near-duplicates, entity/event clustering) are
staged for M2; the helpers below are the building blocks and are already
exercised by tests.
"""

from __future__ import annotations

from typing import Any

from ..core.ids import hamming_distance

NEAR_DUP_HAMMING_THRESHOLD = 3


def exact_key(item: dict[str, Any]) -> tuple[str, str]:
    """Tier-1 identity: (canonical URL, content hash)."""
    return item["provenance"]["url_canonical"], item["provenance"]["content_hash"]


def near_duplicate(item_a: dict[str, Any], item_b: dict[str, Any],
                   threshold: int = NEAR_DUP_HAMMING_THRESHOLD) -> bool:
    """Tier-2 candidate check on stored simhashes. Cheap; runs post-hoc."""
    ha = item_a["provenance"].get("simhash")
    hb = item_b["provenance"].get("simhash")
    if not ha or not hb:
        return False
    return hamming_distance(ha, hb) <= threshold


def cluster_hint(item: dict[str, Any]) -> str | None:
    """Tier-3 placeholder: event/entity cluster key (M2, needs entity extraction)."""
    return None
