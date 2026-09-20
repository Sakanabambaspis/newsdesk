"""Select strategies: rubric-scored story selection (wayfinder tickets 08+09).

The ``select`` stage's strategy plugins. All three share one pipeline
(ticket 08), in order: window (``hours``, default 24) → the 30-item
candidate cap → verdict filter (technical-only when verdicts exist,
``skipped`` fallback — unchanged) → rubric scoring of *all* post-filter
candidates (:func:`~newsdesk.workflow.rubric.score_items`, one LLM pass
with the all-or-nothing mechanical fallback) → admission
(``thresholds.min_score``: stories below the bar are not packable; packs
shrink, never pad) → the strategy picks stories → the material pack in
final episode order under the existing budget caps. The writer contract
is untouched — deep dive = ``pack[0]``, headlines = ``pack[1:4]``.

- **Cluster collapse.** A story is ``story_key`` (the normalized
  headline; whole v1 spec). Collapse groups stories at pick time; each
  cluster fields one representative — highest rubric total, then fresher
  ``published_at``, then lexicographic ``id``, a total order. The
  cluster's size (distinct publishers among its candidate copies in the
  scored, post-filter set) rides the pack card as ``outlets`` — the
  corroboration reason's "carried by N distinct publishers" is the same
  number computed the same way, from :func:`publisher_counts` over the
  same list. Items with no usable title carry the empty key: excluded
  from story counts (so no strategy picks them — strategies pick
  stories), but never dropped from the material. Scoring runs before
  collapse so the corroboration signal counts publishers across the
  whole candidate set.

- **The strategies are separate registered plugins** pinned by
  ``plugin`` on the select stage; an unpinned select stays the legacy
  built-in digest, which is how frozen ``default-morning@1`` keeps its
  characterization-pinned behavior. No settings knob (no consumer).
  ``rubric`` is a required param resolved at pre-flight through the
  rubric catalog (engine); ``k`` and ``hours`` are validated here.
  Rejected upstream: one plugin with a ``mode`` param — an enum switch
  hides each strategy's policy and fakes the closed key sets.

- **Scores, reasons, rubric ref and method are run outputs** (ADR 0001):
  they ride the select artifact (``scoring``), the select report and the
  ``select_scored`` log entry — never catalog state.

Strategies never enter the repair loop: the engine refuses at pre-flight
any check bound where its artifacts cannot exist (so a strategy stage can
never run a doomed repair loop), collapse is true by construction, and
the coverage pair (bound post-compose, measured against the
``admissible`` set this module exposes) remains the defect backstop.

Why no shared ``VersionedCatalog`` base under ``rubric_catalog`` and
``catalog``: the two repos are small concrete twins over different
validators and log vocabularies, and each ticket's pinned semantics read
best in one place; a parameterized base would trade that legibility for
indirection. Revisit if a third catalog appears.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ..llm.base import get_adapter
from ..morning.registries import Registry
from ..pipeline.digest import (candidate_items, classify_verdicts,
                               technical_only, item_card)
from ..pipeline.material import build_material_pack
from ..storage.repo import LogRepo
from .rubric import publisher_counts, rubric_ref, score_items, story_key
from .schema import is_int

SELECT_STRATEGIES = Registry("SELECT_STRATEGIES")


# -- the common pipeline -------------------------------------------------------

def _hours(params: dict[str, Any]) -> int | float:
    hours = params.get("hours", 24)
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) \
            or hours <= 0:
        raise ValueError(f"hours must be a number > 0, got {hours!r}")
    return hours


def _k(params: dict[str, Any], strategy: str) -> int:
    k = params.get("k")
    if not is_int(k) or k < 1:
        raise ValueError(f"{strategy} needs an integer k >= 1, got {k!r}")
    return k


def _representative(copies: list[dict[str, Any]],
                    totals: dict[str, float]) -> dict[str, Any]:
    """A cluster's one packable copy: highest rubric total, then fresher
    ``published_at``, then lexicographic ``id`` (stable multi-pass sort —
    a total order, no heuristics)."""
    ordered = sorted(copies, key=lambda i: i["id"])
    ordered.sort(key=lambda i: i.get("published_at") or "", reverse=True)
    ordered.sort(key=lambda i: -totals[i["id"]])
    return ordered[0]


def _stories(admissible: list[dict[str, Any]], totals: dict[str, float],
             outlets: dict[str, int]) -> list[dict[str, Any]]:
    """Cluster collapse (ticket 08): the admissible set grouped by story
    key; each cluster keeps its representative (carrying ``outlets`` —
    the distinct-publisher count over the scored candidate set, the same
    number the corroboration reason cites), its best total and its
    freshness for the strategy-level orderings. Empty-key items form no
    cluster."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in admissible:
        key = story_key(item.get("title"))
        if key:
            grouped.setdefault(key, []).append(item)
    stories: list[dict[str, Any]] = []
    for key, copies in grouped.items():
        rep = dict(_representative(copies, totals), outlets=outlets[key])
        stories.append({"key": key, "rep": rep,
                        "total": totals[rep["id"]],
                        "published": rep.get("published_at") or ""})
    return stories


def _impact_order(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Impactful = the rubric total: total desc, then fresher, then the
    representative's id (the spec's "then id" — stories inherit their
    representative's total order)."""
    ordered = sorted(stories, key=lambda s: s["rep"]["id"])
    ordered.sort(key=lambda s: s["published"], reverse=True)
    ordered.sort(key=lambda s: -s["total"])
    return ordered


def _trend_order(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trending = corroboration breadth: outlets desc, then total, then
    fresher, then key."""
    ordered = _impact_order(stories)
    ordered.sort(key=lambda s: -s["rep"]["outlets"])
    return ordered


def _pick_top_k(stories: list[dict[str, Any]],
                k: int) -> list[dict[str, Any]]:
    """The k best stories by rubric total (ties: fresher, then id)."""
    return [s["rep"] for s in _impact_order(list(stories))[:k]]


def _pick_mix(stories: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """The trending-impactful construction (ticket 08): ``pack[0]`` is the
    best-impactful story (the deep dive is substance), then ``floor(k/2)``
    trending slots, then impactful fills the rest. Both pools rank the
    same story set, so each backfills the other when it runs dry — any
    slot a dry pool leaves goes to the other pool's next story, and the
    fills end only at k or when the stories are out (packs shrink, never
    pad)."""
    if not stories:
        return []
    impact = _impact_order(list(stories))
    trend = _trend_order(list(stories))
    chosen = [impact[0]]
    keys = {impact[0]["key"]}

    def take(pool: list[dict[str, Any]], limit: int) -> None:
        added = 0
        for story in pool:
            if added >= limit:
                break
            if story["key"] not in keys:
                chosen.append(story)
                keys.add(story["key"])
                added += 1

    take(trend, k // 2)
    take(impact, k - len(chosen))  # impactful fills the rest — never past k
    return [s["rep"] for s in chosen]


def _pick_single(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exactly one story, the top total — never padded; empty when no
    story is admissible (the quiet-day short episode)."""
    if not stories:
        return []
    return [_impact_order(list(stories))[0]["rep"]]


def _select(session, settings: Any, rubric: dict[str, Any],
            params: dict[str, Any], *, method: str,
            pick: Callable[[list[dict[str, Any]]],
                           list[dict[str, Any]]]) -> dict[str, Any]:
    """The common pipeline (ticket 08), ending in the strategy's pick and
    the material pack in final episode order. Returns the select
    artifact: the writer's pack, the admissible set (the coverage
    floors' base) and the full scoring — and logs ``select_scored``."""
    hours = _hours(params)
    candidates, window_count = candidate_items(session, hours=hours)
    adapter = get_adapter(settings)
    verdicts, verdict_method = classify_verdicts(adapter, candidates)
    filtered = technical_only(candidates, verdicts)

    scoring = score_items(rubric, filtered, adapter, window_hours=hours)
    min_score = (rubric.get("thresholds") or {}).get("min_score") or 0.0
    totals = {s["id"]: s["total"] for s in scoring["scores"]}
    admissible = [i for i in filtered if totals.get(i["id"], 0.0) >= min_score]

    pack = build_material_pack(
        pick(_stories(admissible, totals, publisher_counts(filtered))),
        verdicts or None)
    digest: dict[str, Any] = {
        "method": method,
        "rubric": rubric_ref(rubric),
        "min_score": min_score,
        "window_hours": hours,
        "items_in_window": window_count,
        "verdict_method": verdict_method,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scoring": scoring,
        "admissible": [item_card(i, verdicts) for i in admissible],
        "material_pack": {
            "method": pack["method"],
            "verdict_filter": pack["verdict_filter"],
            "generated_at": pack["generated_at"],
            "items": pack["items"],
            # the selection stats: shrinkage from candidates to pack is
            # visible per run (ticket 09 acceptance)
            "stats": {
                "candidates": len(candidates),
                "after_filter": len(admissible),
                "in_pack": pack["stats"]["in_pack"],
                "dropped_over_budget": pack["stats"]["dropped_over_budget"],
                "chars": pack["stats"]["chars"],
            },
        },
    }
    LogRepo(session).append("select_scored", {
        "strategy": method,
        "rubric": rubric_ref(rubric),
        "scoring_method": scoring["method"],
        "min_score": min_score,
        "window_hours": hours,
        "items_in_window": window_count,
        "verdict_method": verdict_method,
        "candidates": len(candidates),
        "after_filter": len(admissible),
        "in_pack": pack["stats"]["in_pack"],
        "items": [{"id": i["id"], "outlets": i.get("outlets")}
                  for i in pack["items"]],
        "scores": scoring["scores"],
        **({"unassessed": scoring["unassessed"]}
           if "unassessed" in scoring else {}),
        **({"llm_error": scoring["llm_error"]}
           if "llm_error" in scoring else {}),
    })
    return digest


# -- the three strategies --------------------------------------------------------

def top_k_interesting(session, settings: Any, rubric: dict[str, Any],
                      params: dict[str, Any]) -> dict[str, Any]:
    """The ``k`` best stories by rubric total. "Interesting" is the
    rubric total, nothing else."""
    k = _k(params, "top-k-interesting")
    return _select(session, settings, rubric, params,
                   method="top-k-interesting",
                   pick=lambda stories: _pick_top_k(stories, k))


def trending_impactful_mix(session, settings: Any, rubric: dict[str, Any],
                           params: dict[str, Any]) -> dict[str, Any]:
    """Deep dive = best-impactful (the rubric total), then ``floor(k/2)``
    trending slots (corroboration breadth), then impactful fills — dedup
    by story, cross-backfill when a pool runs dry."""
    k = _k(params, "trending-impactful-mix")
    return _select(session, settings, rubric, params,
                   method="trending-impactful-mix",
                   pick=lambda stories: _pick_mix(stories, k))


def single_deep_dive(session, settings: Any, rubric: dict[str, Any],
                     params: dict[str, Any]) -> dict[str, Any]:
    """Exactly one story: the top total. No admissible story → empty pack
    → the quiet-day short episode. Never padded."""
    return _select(session, settings, rubric, params,
                   method="single-deep-dive", pick=_pick_single)


SELECT_STRATEGIES.register("top-k-interesting", top_k_interesting,
                           stage="select",
                           params=("rubric", "k", "hours"))
SELECT_STRATEGIES.register("trending-impactful-mix", trending_impactful_mix,
                           stage="select",
                           params=("rubric", "k", "hours"))
SELECT_STRATEGIES.register("single-deep-dive", single_deep_dive,
                           stage="select", params=("rubric", "hours"))
