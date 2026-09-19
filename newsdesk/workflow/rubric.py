"""The Rubric: pure-data taste, and the scorer contract (wayfinder 07).

A rubric is the inspectable standard selection scores against (ADR 0001):
dimensions with weights, anchor examples, and one admission threshold —
rich enough that conversational preference vocabulary ("I like deep
technical substance, hate funding hype") compiles into it as dimensions
and anchors. Decisions encoded here:

- **Scorer I/O.** ``score_items(rubric, items, adapter, *, window_hours,
  now)`` returns per item a newsdesk-computed ``total`` (weighted mean of
  the *scored* dimensions, so it always reads 0..1 whatever the weights
  sum to) plus a ``dimensions`` map of ``{score, reason}`` entries. The
  model never emits totals and never places an item without a stored,
  inspectable reason — per dimension, so the audit trail says *why* each
  component scored as it did. Scores are run outputs: they ride the
  select stage's report and the log; they are never catalog state (the
  rubric is versioned data, the scores are recomputable per run).

- **Proxied dimensions are computed, never asked.** A dimension may
  declare one mechanical proxy from the closed set below — ``relevance``
  (the recorded weighted watchlist-term match), ``recency``
  (``1 - age/window``, clamped to 0..1), ``corroboration`` (distinct
  publishers sharing the story key, capped — a story carried by three
  outlets is a different fact than a lone post). Proxies are computed in
  *every* path; the LLM is asked only about the unproxied dimensions, and
  a rubric with none needs no model at all.

- **The no-key fallback is the same formula minus the model.** If the
  adapter reports unconfigured, raises ``LLMError`` (its failure
  contract — any other exception is a bug and stays loud), or fails to
  produce a complete score for every item × unproxied dimension, the
  whole pass falls back to mechanical: proxied dimensions score from
  their signals (a malformed value — an unparseable timestamp, a
  non-numeric relevance — scores 0.0 with a visible reason; a degenerate
  input degrades the item, it never crashes the run), unproxied ones are *excluded* (weights renormalize over
  what was scored — an unassessed dimension is not a zero) and listed in
  ``unassessed``. The fallback is all-or-nothing: model scores and
  mechanical fills are never mixed, they are not comparable. The cause
  stays visible in ``llm_error``, mirroring the digest's verdict-method
  posture. ``thresholds`` ride the data unused here: the admission
  threshold is selection policy, consumed by the strategies (tickets
  08/09) and the score-preview tool.

- **Attachment is by reference, not inline.** A workflow's select stage
  names a rubric by ref string (``default`` floats at the computed
  latest, ``default@1`` pins — the ticket-04 float/pin semantics),
  resolved at run start through the rubric catalog; rubrics themselves
  are catalog-versioned with the same semantics as workflows. A missing
  or retired ref fails the run loudly at pre-flight — never a silent
  fallback. Storage mechanics are ticket 09's build; the ref-string
  contract is fixed here.

- ``story_key`` — the normalized-headline story identity the syndication
  check (ticket 05) and the corroboration signal share — lives here so
  there is one definition; ticket 08 owns its formal spec.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..llm.base import LLMError
from .schema import WORKFLOW_NAME_RE, _is_int

RUBRIC_FORMAT_VERSION = 1

# A story corroborated by this many distinct outlets scores the full 1.0;
# n outlets score min(n, cap)/cap. A constant, not a knob: the reason
# strings cite the observed count, the module docstring the formula.
CORROBORATION_CAP = 3

DIMENSION_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")

_TOP_KEYS = {"format_version", "name", "version", "title", "dimensions",
             "thresholds"}
_DIMENSION_KEYS = {"name", "description", "weight", "proxy", "anchors"}
_ANCHOR_KEYS = {"score", "example"}


class RubricError(Exception):
    """A rubric is invalid; message lists every violation found."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unproxied(rubric: dict[str, Any]) -> list[str]:
    """The dimension names the model is asked to score."""
    return [d["name"] for d in rubric["dimensions"] if not d.get("proxy")]


def rubric_ref(rubric: dict[str, Any]) -> str:
    """The rubric's ``name@version`` identity string."""
    return f"{rubric['name']}@{rubric['version']}"


def shipped_rubric_path(name: str, version: int) -> Path:
    """Where a shipped rubric lives in the package."""
    return Path(__file__).parent / "rubrics" / f"{name}@{version}.json"


def load_shipped_rubric(name: str, version: int) -> dict[str, Any]:
    """Load a shipped package rubric ``name@version``, validated."""
    path = shipped_rubric_path(name, version)
    doc = json.loads(path.read_text(encoding="utf-8"))
    require_valid_rubric(doc)
    return doc


def validate_rubric(doc: Any) -> list[str]:
    """Return every violation of the rubric schema, or an empty list."""
    if not isinstance(doc, dict):
        return ["rubric must be a JSON object"]
    errors: list[str] = []
    unknown = sorted(set(doc) - _TOP_KEYS)
    if unknown:
        errors.append(f"unknown keys {', '.join(unknown)}")
    version = doc.get("format_version")
    if version != RUBRIC_FORMAT_VERSION:
        errors.append(f"format_version must be {RUBRIC_FORMAT_VERSION}, "
                      f"got {version!r}")
    name = doc.get("name")
    if not (isinstance(name, str) and WORKFLOW_NAME_RE.fullmatch(name)):
        errors.append(f"name must match {WORKFLOW_NAME_RE.pattern}, "
                      f"got {name!r}")
    rev = doc.get("version")
    if not _is_int(rev) or rev < 1:
        errors.append("version must be an integer >= 1")
    title = doc.get("title")
    if title is not None and not isinstance(title, str):
        errors.append("title must be a string")
    thresholds = doc.get("thresholds")
    if thresholds is not None:
        if not isinstance(thresholds, dict):
            errors.append("thresholds must be an object")
        else:
            extra = sorted(set(thresholds) - {"min_score"})
            if extra:
                errors.append(f"thresholds: unknown keys {', '.join(extra)}")
            floor = thresholds.get("min_score")
            if floor is not None and (not _is_number(floor)
                                      or not 0.0 <= floor <= 1.0):
                errors.append("thresholds: min_score must be a number "
                              "in 0..1")
    dimensions = doc.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        errors.append("dimensions must be a non-empty list")
    else:
        names: set[str] = set()
        for dimension in dimensions:
            errors.extend(_dimension_errors(dimension))
            if (isinstance(dimension, dict)
                    and isinstance(dimension.get("name"), str)):
                if dimension["name"] in names:
                    errors.append(f"duplicate dimension "
                                  f"'{dimension['name']}'")
                names.add(dimension["name"])
    return errors


def _dimension_errors(dimension: Any) -> list[str]:
    if not isinstance(dimension, dict):
        return ["each dimension must be an object"]
    name = dimension.get("name") if isinstance(dimension.get("name"), str) \
        else "(unnamed)"
    errors: list[str] = []
    unknown = sorted(set(dimension) - _DIMENSION_KEYS)
    if unknown:
        errors.append(f"dimension '{name}': unknown keys {', '.join(unknown)}")
    dname = dimension.get("name")
    if not (isinstance(dname, str) and DIMENSION_NAME_RE.fullmatch(dname)):
        errors.append(f"dimension '{name}': name must match "
                      f"{DIMENSION_NAME_RE.pattern}")
    description = dimension.get("description")
    if not (isinstance(description, str) and description.strip()):
        errors.append(f"dimension '{name}': description must be a "
                      f"non-empty string (it is the dimension's inspectable "
                      f"definition; unproxied dimensions are quoted "
                      f"verbatim in the scorer prompt)")
    weight = dimension.get("weight")
    if not _is_number(weight) or weight <= 0:
        errors.append(f"dimension '{name}': weight must be a number > 0 "
                      f"(a stated preference with zero weight should be "
                      f"deleted, not zeroed)")
    proxy = dimension.get("proxy")
    if proxy is not None and proxy not in PROXY_SIGNALS:
        errors.append(f"dimension '{name}': proxy must be one of "
                      f"{', '.join(PROXY_SIGNALS)}, got {proxy!r}")
    anchors = dimension.get("anchors")
    if anchors is not None:
        if not isinstance(anchors, list) or not anchors:
            errors.append(f"dimension '{name}': anchors must be a "
                          f"non-empty list when present")
        else:
            levels: set[float] = set()
            for anchor in anchors:
                errors.extend(f"dimension '{name}', anchor: {e}" for e in
                              _anchor_errors(anchor))
                if (isinstance(anchor, dict) and _is_number(anchor.get("score"))
                        and 0.0 <= anchor["score"] <= 1.0):
                    if anchor["score"] in levels:
                        errors.append(f"dimension '{name}': duplicate "
                                      f"anchor score {anchor['score']}")
                    levels.add(anchor["score"])
    return errors


def _anchor_errors(anchor: Any) -> list[str]:
    if not isinstance(anchor, dict):
        return ["must be an object"]
    errors: list[str] = []
    unknown = sorted(set(anchor) - _ANCHOR_KEYS)
    if unknown:
        errors.append(f"unknown keys {', '.join(unknown)}")
    score = anchor.get("score")
    if not _is_number(score) or not 0.0 <= score <= 1.0:
        errors.append("score must be a number in 0..1")
    example = anchor.get("example")
    if not (isinstance(example, str) and example.strip()):
        errors.append("example must be a non-empty string")
    return errors


def require_valid_rubric(doc: Any) -> None:
    """Raise :class:`RubricError` listing all violations, if any."""
    errors = validate_rubric(doc)
    if errors:
        raise RubricError("invalid rubric:\n- " + "\n- ".join(errors))


# -- Scoring -------------------------------------------------------------------

def story_key(title: Any) -> str:
    """A story's identity: the normalized headline. Wire syndication
    reprints share the wire headline across outlets, so collapsed clusters
    show up as one key (ticket 05's provisional rule; ticket 08 owns the
    formal spec)."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(title or "").lower())
                    .split())


def _publisher_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Distinct outlets per story key across the candidate set."""
    outlets: dict[str, set[str]] = {}
    for item in items:
        outlets.setdefault(story_key(item.get("title")), set()).add(
            str(item.get("publisher") or "unknown"))
    return {key: len(value) for key, value in outlets.items()}


def _signal_relevance(item: dict[str, Any], *, window_hours: float,
                      now: datetime, counts: dict[str, int]) \
        -> tuple[float, str]:
    relevance = item.get("relevance")
    if relevance is None:
        return 0.0, "no watchlist terms configured"
    try:
        value = float(relevance)
    except (TypeError, ValueError):
        return 0.0, f"unreadable relevance {relevance!r}"
    return value, f"watchlist relevance {value:.2f}"


def _signal_recency(item: dict[str, Any], *, window_hours: float,
                    now: datetime, counts: dict[str, int]) \
        -> tuple[float, str]:
    when = item.get("published_at")
    if not when:
        return 0.0, "no publish timestamp available"
    try:
        published = datetime.fromisoformat(str(when))
    except ValueError:
        return 0.0, f"unreadable publish timestamp {when!r}"
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - published).total_seconds() / 3600)
    score = min(1.0, max(0.0, 1 - age_hours / window_hours))
    return score, f"published {age_hours:.1f}h ago (window {window_hours:g}h)"


def _signal_corroboration(item: dict[str, Any], *, window_hours: float,
                          now: datetime, counts: dict[str, int]) \
        -> tuple[float, str]:
    n = counts.get(story_key(item.get("title")), 1)
    score = min(n, CORROBORATION_CAP) / CORROBORATION_CAP
    return score, (f"carried by {n} distinct "
                   f"publisher{'s' if n != 1 else ''}")


# Uniform signal signature (item, *, window_hours, now, counts); the closed
# proxy set is the map's keys — one definition, no second switch to update.
_SIGNALS = {"relevance": _signal_relevance,
            "recency": _signal_recency,
            "corroboration": _signal_corroboration}
PROXY_SIGNALS = tuple(_SIGNALS)


def _score_item(rubric: dict[str, Any], item: dict[str, Any],
                publisher_counts: dict[str, int], *, window_hours: float,
                now: datetime,
                model_dims: dict[str, Any] | None = None) \
        -> dict[str, dict[str, Any]] | None:
    """One item's ``{dimension: {score, reason}}`` map.

    Proxied dimensions come from their signals in every path. ``model_dims
    is None`` is the mechanical path: unproxied dimensions are simply
    excluded (weights renormalize — an unassessed dimension is not a
    zero). Otherwise each unproxied dimension must carry a usable model
    score — a number in 0..1 plus a non-empty reason — and one bad or
    missing entry fails the whole pass (returns None): scores are only
    comparable across items when they share a basis.
    """
    dims: dict[str, dict[str, Any]] = {}
    for dimension in rubric["dimensions"]:
        name, proxy = dimension["name"], dimension.get("proxy")
        signal = _SIGNALS.get(proxy) if proxy else None
        if signal is not None:
            value, reason = signal(item, window_hours=window_hours, now=now,
                                   counts=publisher_counts)
        elif model_dims is None:
            continue  # mechanical path: no proxy, no score
        else:
            modelled = model_dims.get(name)
            if not isinstance(modelled, dict):
                return None
            value, reason = modelled.get("score"), modelled.get("reason")
            if not _is_number(value) or not 0.0 <= value <= 1.0:
                return None
            if not isinstance(reason, str) or not reason.strip():
                return None
            value, reason = float(value), reason.strip()
        dims[name] = {"score": round(value, 4), "reason": reason}
    return dims


def _weighted_total(dimensions: list[dict[str, Any]],
                    scored: dict[str, dict[str, Any]]) -> float:
    """Weighted mean over the scored dimensions — 0..1 whatever the
    weights sum to."""
    used = [d for d in dimensions if d["name"] in scored]
    weight = sum(d["weight"] for d in used)
    if not weight:
        return 0.0
    total = sum(d["weight"] * scored[d["name"]]["score"] for d in used)
    return round(total / weight, 4)


def _score_all(rubric: dict[str, Any], items: list[dict[str, Any]],
               *, window_hours: float, now: datetime,
               model_scores: dict[str, dict[str, Any]] | None = None) \
        -> list[dict[str, Any]] | None:
    """Score every item; None when any model entry is missing or unusable
    (the all-or-nothing rule)."""
    counts = _publisher_counts(items)
    scores: list[dict[str, Any]] = []
    for item in items:
        # a missing entry becomes {} — an unmodelled item fails the pass
        # (all-or-nothing), it never silently takes the mechanical path
        model_dims = model_scores.get(item["id"], {}) \
            if model_scores is not None else None
        dims = _score_item(rubric, item, counts, window_hours=window_hours,
                           now=now, model_dims=model_dims)
        if dims is None:
            return None
        scores.append({"id": item["id"],
                       "total": _weighted_total(rubric["dimensions"], dims),
                       "dimensions": dims})
    return scores


def mechanical_scores(rubric: dict[str, Any], items: list[dict[str, Any]],
                      *, window_hours: float = 24,
                      now: datetime | None = None) -> dict[str, Any]:
    """Score ``items`` with the mechanical signals only — the no-key
    fallback, and the whole truth for an all-proxied rubric."""
    require_valid_rubric(rubric)
    if now is None:
        now = datetime.now(timezone.utc)
    scores = _score_all(rubric, items, window_hours=window_hours, now=now)
    result: dict[str, Any] = {"rubric": rubric_ref(rubric),
                              "method": "mechanical", "scores": scores}
    if _unproxied(rubric):
        result["unassessed"] = _unproxied(rubric)
    return result


def score_items(rubric: dict[str, Any], items: list[dict[str, Any]],
                adapter: Any, *, window_hours: float = 24,
                now: datetime | None = None) -> dict[str, Any]:
    """Score items against the rubric; the one scorer both paths share.

    Proxied dimensions are computed mechanically in every path; the
    adapter is asked only about the unproxied ones. Any adapter failure —
    unconfigured, raised, unparseable, or an incomplete pass — falls back
    to :func:`mechanical_scores` whole, with the cause in ``llm_error``.
    Totals are always computed here, never taken from the model.
    """
    require_valid_rubric(rubric)
    if not items:
        return {"rubric": rubric_ref(rubric), "method": "skipped:no_items",
                "scores": []}
    if now is None:
        now = datetime.now(timezone.utc)
    unproxied = _unproxied(rubric)

    def fallback(cause: str) -> dict[str, Any]:
        result = mechanical_scores(rubric, items, window_hours=window_hours,
                                   now=now)
        result["llm_error"] = cause
        return result

    if not unproxied:  # nothing to ask: the rubric is fully mechanical
        return mechanical_scores(rubric, items, window_hours=window_hours,
                                 now=now)
    try:
        result = adapter.score_rubric(rubric, items)
    except LLMError as exc:
        return fallback(str(exc))
    if result.get("error"):
        return fallback(str(result["error"]))
    model_scores = {s["id"]: (s.get("dimensions") or {})
                    for s in result.get("scores") or []}
    scores = _score_all(rubric, items, window_hours=window_hours, now=now,
                        model_scores=model_scores)
    if scores is None:
        covered = sum(1 for item in items
                      for name in unproxied
                      if isinstance(model_scores.get(item["id"], {})
                                    .get(name), dict))
        needed = len(items) * len(unproxied)
        return fallback(f"model pass incomplete — did not score every item "
                        f"on every unproxied dimension "
                        f"({covered}/{needed} usable)")
    return {"rubric": rubric_ref(rubric),
            "method": f"llm:{getattr(adapter, 'name', 'unknown')}",
            "scores": scores}
