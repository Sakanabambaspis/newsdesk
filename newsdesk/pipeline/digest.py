"""The daily briefing: what is worth following, and how to go deeper.

Selection is mechanical (recency window, watchlist relevance, term groups)
so it works with zero configuration; the narrative layer is one grounded
LLM call over the pre-ranked items, with a clearly-labeled extractive
fallback when no LLM is configured. Every section cites item IDs — the
drill-down path is `newsdesk item <id>` / `newsdesk summarize <id>`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import Settings
from ..llm.base import VERDICT_VALUES, LLMError, get_adapter
from ..storage.repo import ItemRepo, LogRepo, WatchlistRepo
from .material import build_material_pack
from .normalize import relevance_score

SNIPPET_CHARS = 400
MAX_LLM_ITEMS = 30
MAX_THEMES = 8


def _in_window(row, cutoff: datetime) -> bool:
    when = row.published_at or row.retrieved_at
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= cutoff


def _scan(session, hours: int, scan_cap: int = 1200) -> list:
    """Items in the window, freshest first (recent() is already ranked)."""
    rows = ItemRepo(session).recent(limit=scan_cap)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [r for r in rows if _in_window(r, cutoff)]


def _term_matches(haystack: str,
                  include_terms: list[tuple[str, float]]) -> list[tuple[str, float]]:
    low = haystack.lower()
    return [(t, w) for t, w in include_terms if t in low]


def _collect(session, hours: int, include_terms: list[tuple[str, float]]) \
        -> list[dict[str, Any]]:
    """Window items as compact dicts, ranked: relevance desc, then freshness."""
    ranked: list[dict[str, Any]] = []
    for row in _scan(session, hours):
        text = row.text or ""
        matches = _term_matches(f"{row.title or ''}\n{text}", include_terms)
        relevance = (row.analysis or {}).get("relevance")
        if relevance is None:
            relevance = relevance_score(row.title or "", text, include_terms)
        when = (row.published_at or row.retrieved_at)
        if when and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        ranked.append({
            "id": row.id,
            "title": row.title or "(untitled)",
            "publisher": row.publisher or "unknown",
            "url": row.url,
            "kind": row.kind,
            "published_at": when.isoformat() if when else None,
            "relevance": relevance if relevance is not None else 0.0,
            "matched_terms": sorted({t for t, _ in matches}),
            "snippet": " ".join(text.split())[:SNIPPET_CHARS],
        })
    ranked.sort(key=lambda i: (-float(i["relevance"]), i["published_at"] or ""), )
    return ranked


def _group_themes(items: list[dict[str, Any]],
                  include_terms: list[tuple[str, float]]) \
        -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group items by their highest-weight matched watchlist term."""
    weight = {t: w for t, w in include_terms}
    themes: dict[str, list[dict[str, Any]]] = {}
    loose: list[dict[str, Any]] = []
    for item in items:
        keys = [t for t, _ in sorted(
            ((t, weight.get(t, 1.0)) for t in item["matched_terms"]),
            key=lambda kv: -kv[1])]
        if keys:
            themes.setdefault(keys[0], []).append(item)
        else:
            loose.append(item)
    grouped = sorted(
        ({"term": k, "items": v} for k, v in themes.items()),
        key=lambda g: (-sum(i["relevance"] for i in g["items"]), -len(g["items"])),
    )
    return grouped[:MAX_THEMES], loose


def _fallback_digest(items: list[dict[str, Any]],
                     themes: list[dict[str, Any]],
                     loose: list[dict[str, Any]],
                     window_count: int) -> dict[str, Any]:
    """No-LLM briefing: theme groups + top items, clearly labeled."""
    sections = [{
        "theme": f"{g['term']} ({len(g['items'])} item"
                 f"{'s' if len(g['items']) != 1 else ''})",
        "why": "Grouped by watchlist term; no LLM configured, so no narrative.",
        "item_ids": [i["id"] for i in g["items"][:6]],
    } for g in themes]
    return {
        "overview": f"{window_count} items in the window, top {len(items)} "
                    f"considered; {len(themes)} watchlist themes matched. This "
                    "briefing is extractive (no LLM configured) — set "
                    "NEWSDESK_LLM_BASE_URL and NEWSDESK_LLM_API_KEY for an "
                    "analyst narrative.",
        "worth_following": sections,
        "also_noteworthy": [f"{i['title']} ({i['publisher']}, {i['id']})"
                            for i in loose[:5]],
        "noise": "Items not matching any watchlist term were skipped from themes.",
        "method": "extractive",
    }


def _guard_verdicts(entries: Any, items_by_id: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Editorial guard (ADR 0001): only provided ids may carry a verdict."""
    guarded: dict[str, dict[str, str]] = {}
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("id") in items_by_id:
            guarded[entry["id"]] = {"verdict": entry["verdict"], "reason": entry["reason"]}
    return guarded


def candidate_items(session, *, hours: int = 24,
                    limit: int = MAX_LLM_ITEMS) -> tuple[list[dict[str, Any]], int]:
    """The selection candidate set (the common pipeline's first stages,
    W3): items in the ``hours`` window ranked relevance-desc, capped at
    ``min(limit, MAX_LLM_ITEMS)`` — the cap holds no matter what a caller
    asks for (ADR 0001 bounds the writer's injection surface). Shared by
    the legacy digest and the select strategies, so every path ranks and
    caps identically. Returns ``(candidates, items_in_window)`` with the
    pre-cap window count for the report."""
    include_terms = WatchlistRepo(session).include_terms()
    ranked = _collect(session, hours, include_terms)
    return ranked[:min(limit, MAX_LLM_ITEMS)], len(ranked)


def classify_verdicts(adapter: Any,
                      items: list[dict[str, Any]]) \
        -> tuple[dict[str, dict[str, str]], str]:
    """The editorial verdict pass (ADR 0001): guarded per-item verdicts
    plus the method string. Unconfigured/failed passes degrade to no
    verdicts with the cause visible in the method — the caller's filter
    then treats every item as admissible (the ``skipped`` fallback)."""
    if not items:
        return {}, "skipped:no_items"
    try:
        result = adapter.classify_verdicts(items)
    except LLMError as exc:
        # LLMError messages are safe to log by contract, and the report
        # is the CI log — keep the cause visible so a red LLM stage is
        # diagnosable from the run alone.
        result = {"error": f"llm_error:{exc}"[:420]}
    if result.get("error"):
        return {}, f"skipped:{result['error']}"
    items_by_id = {i["id"]: i for i in items}
    return _guard_verdicts(result.get("verdicts"), items_by_id), \
        f"llm:{adapter.name}"


def technical_only(items: list[dict[str, Any]],
                   verdicts: dict[str, dict[str, str]]) \
        -> list[dict[str, Any]]:
    """The verdict filter: technical-only when verdicts exist, otherwise
    every item passes (the ``skipped`` fallback) — one definition for the
    pack builder and the strategies' admissible set."""
    if not verdicts:
        return list(items)
    return [i for i in items
            if (verdicts.get(i["id"]) or {}).get("verdict") == "technical"]


def item_card(item: dict[str, Any],
              verdicts: dict[str, dict[str, str]]) -> dict[str, Any]:
    """One full item card for drill-down, in ranked order."""
    return {
        "id": item["id"], "title": item["title"], "publisher": item["publisher"],
        "url": item["url"], "relevance": item["relevance"],
        "matched_terms": item["matched_terms"], "published_at": item["published_at"],
        "verdict": (verdicts.get(item["id"]) or {}).get("verdict"),
        "verdict_reason": (verdicts.get(item["id"]) or {}).get("reason"),
    }


def build_daily_digest(session, settings: Settings, *, hours: int = 24,
                       limit: int = 30, adapter: Any = None) -> dict[str, Any]:
    """Build the briefing and log it. Returns the digest dict."""
    selected, window_count = candidate_items(session, hours=hours, limit=limit)
    themes, loose = _group_themes(selected, WatchlistRepo(session).include_terms())

    adapter = adapter or get_adapter(settings)
    verdicts, verdict_method = classify_verdicts(adapter, selected)

    try:
        briefing = adapter.summarize_digest(selected)
    except LLMError as exc:
        briefing = {"error": f"llm_error:{exc}"[:420]}
    if str(briefing.get("error", "")).startswith(
            ("llm_not_configured", "llm_error", "no_items")):
        briefing = _fallback_digest(selected, themes, loose, window_count)
    else:
        # Grounding guard: keep only citations that refer to provided items.
        for section in briefing.get("worth_following", []):
            section["item_ids"] = [i for i in section.get("item_ids", [])
                                   if i in {x["id"] for x in selected}]
        briefing.setdefault("method", f"llm:{adapter.name}")

    verdict_counts = {v: sum(1 for x in verdicts.values() if x["verdict"] == v)
                      for v in VERDICT_VALUES}
    pack = build_material_pack(selected, verdicts or None)
    # what selection could legally have packed (post-verdict-filter; the
    # coverage checks' "offered" base, ticket 08)
    admissible = technical_only(selected, verdicts)

    briefing["verdict_method"] = verdict_method
    briefing["material_pack"] = {
        "method": pack["method"], "verdict_filter": pack["verdict_filter"],
        "verdict_counts": verdict_counts, **pack["stats"],
        # the full pack: exactly what the briefing writer may see (ADR 0001)
        "items": pack["items"],
    }
    briefing.update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": hours,
        "items_in_window": window_count,
        "items_considered": len(selected),
        # full item cards for drill-down, in ranked order
        "items": [item_card(i, verdicts) for i in selected],
        # the admissible set: same cards, post-filter — what the coverage
        # floors scale to (ticket 08/09)
        "admissible": [item_card(i, verdicts) for i in admissible],
    })
    LogRepo(session).append("daily_digest_built", {
        "window_hours": hours, "items_in_window": window_count,
        "items_considered": len(selected), "method": briefing.get("method"),
        "overview": (briefing.get("overview") or "")[:300],
        "verdict_method": verdict_method,
        "verdict_counts": verdict_counts,
        "verdicts": [{"id": i["id"], **verdicts[i["id"]]}
                     for i in selected if i["id"] in verdicts],
        "material_pack": briefing["material_pack"],
    })
    return briefing


def render_markdown(digest: dict[str, Any]) -> str:
    """Human-readable briefing with drill-down hints."""
    lines: list[str] = []
    lines.append(f"# Newsdesk briefing — {digest['generated_at'][:16]}Z "
                 f"(last {digest['window_hours']}h, "
                 f"{digest['items_in_window']} items in window)")
    lines.append("")
    lines.append(digest.get("overview", ""))
    if digest.get("verdict_method"):
        counts = (digest.get("material_pack") or {}).get("verdict_counts") or {}
        lines.append("")
        lines.append(f"_Verdicts: {counts.get('technical', 0)} technical / "
                     f"{counts.get('hype', 0)} hype / "
                     f"{counts.get('tangential', 0)} tangential — "
                     f"{digest['verdict_method']}_")
    lines.append("")
    lines.append("## Worth following")
    for section in digest.get("worth_following", []):
        lines.append(f"### {section.get('theme', '')}")
        if section.get("why"):
            lines.append(section["why"])
        for item_id in section.get("item_ids", []):
            card = next((i for i in digest["items"] if i["id"] == item_id), None)
            if card:
                rel = card["relevance"]
                lines.append(f"- **{card['title']}** — {card['publisher']} "
                             f"(relevance {rel:.2f})  ")
                lines.append(f"  {card['url']}  ")
                lines.append(f"  `{card['id']}` → detail: "
                             f"`newsdesk item {card['id']}` · "
                             f"`newsdesk summarize {card['id']}`")
        lines.append("")
    if digest.get("also_noteworthy"):
        lines.append("## Also noteworthy")
        lines.extend(f"- {n}" for n in digest["also_noteworthy"])
        lines.append("")
    if digest.get("noise"):
        lines.append(f"_Noise: {digest['noise']}_")
    return "\n".join(lines)
