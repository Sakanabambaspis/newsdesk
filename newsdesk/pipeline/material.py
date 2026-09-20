"""MaterialPack — the curated context one briefing is written from (ADR 0001).

The pack is a first-class artifact with a mechanical, testable truncation
policy, so any script can be traced to exactly what the writer saw and the
writer's prompt-injection surface is bounded to these items and nothing else.
Rules, applied in order: verdict filter (technical only), digest rank order
(relevance desc), item cap, whole-pack character budget (first-fit prefix).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

PACK_METHOD = "mechanical-v1"
PACK_MAX_ITEMS = 30  # same cap as the digest's LLM candidate set
PACK_ITEM_CHARS = 1200  # per-item text ceiling
PACK_TOTAL_CHARS = 24_000  # whole-pack text ceiling (~6k tokens)


def build_material_pack(ranked: list[dict[str, Any]],
                        verdicts: dict[str, dict[str, str]] | None = None, *,
                        max_items: int = PACK_MAX_ITEMS,
                        item_chars: int = PACK_ITEM_CHARS,
                        total_chars: int = PACK_TOTAL_CHARS) -> dict[str, Any]:
    """Curate ranked digest items into the writer's context pack.

    ``verdicts`` maps item id -> {"verdict", "reason"}. When it is empty or
    None (no LLM configured, or the verdict pass failed) the filter is
    skipped and the pack falls back to relevance order, clearly labeled.
    """
    items = list(ranked)
    verdict_filter = "technical"
    if not verdicts:
        verdict_filter = "skipped"
    else:
        items = [i for i in items
                 if (verdicts.get(i["id"]) or {}).get("verdict") == "technical"]
    considered = items[:max_items]

    pack_items: list[dict[str, Any]] = []
    total = 0
    for item in considered:
        text = f"{item.get('title') or ''}\n{(item.get('snippet') or '')[:item_chars]}".strip()
        if total + len(text) > total_chars:
            break  # rank-order prefix that fits; everything later is dropped
        verdict = (verdicts or {}).get(item["id"]) or {}
        card = {
            "id": item["id"],
            "title": item.get("title"),
            "publisher": item.get("publisher"),
            "url": item.get("url"),
            "relevance": item.get("relevance"),
            "matched_terms": item.get("matched_terms") or [],
            "published_at": item.get("published_at"),
            "verdict": verdict.get("verdict"),
            "verdict_reason": verdict.get("reason"),
            "text": text,
        }
        if item.get("outlets") is not None:
            # cluster breadth, set by the W3 select strategies on their
            # collapsed representatives; the legacy digest has no clusters
            card["outlets"] = item["outlets"]
        pack_items.append(card)
        total += len(text)

    return {
        "method": PACK_METHOD,
        "verdict_filter": verdict_filter,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": pack_items,
        "stats": {
            "candidates": len(ranked),
            "after_filter": len(items),
            "in_pack": len(pack_items),
            "dropped_over_budget": len(considered) - len(pack_items),
            "chars": total,
        },
    }
