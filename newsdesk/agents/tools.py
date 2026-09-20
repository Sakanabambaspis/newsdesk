"""The agent tool surface, implemented once (wayfinder tickets 13 + 14).

Every tool is **one host-neutral function**: ``fn(session, settings, ...)
-> dict`` — no surface knowledge, no framework imports, no actor argument
(``actor="agent"`` is hard-coded here so a harness cannot forge
``user``/``system``; the adapter supplies only ``via``, ``"mcp"`` or
``"http"``). The spec — name, description, args, the ``http`` route, the
``mutating`` flag — is module-level metadata carried *by* the
implementation (the :func:`tool` decorator), so a tool cannot exist
without its description: ``TOOL_SPECS`` (``agents.protocol``) is generated
from this registry, not maintained beside it. The HTTP routes
(``api.app``) and the MCP functions (``mcp_server``) stay hand-written
thin adapters — FastAPI and FastMCP both need concrete typed signatures
to produce their schemas — but only parse, delegate, and return.

Etymology of the guarantees the tools inherit rather than re-check:

- **Validation-on-save lives in the repo layer** (schema, then static
  bindings, then the serialized-size cap — ``workflow.catalog``), so CLI,
  seed and tools fail identically. The one tool-side addition is the
  *opt-in* smoke: ``create_workflow(dry_run=True)`` runs the candidate
  document once through a real dry run **before storing it** and refuses
  the save on any stage failure — the caller must ask, because a smoke
  collects (network + tokens) and a save must never surprise you with
  that.
- **The run tool cannot publish, structurally.** ``run_workflow`` has no
  publish and no dry-run parameter at all; it passes ``dry_run=True``
  unconditionally, so the engine breaks its stage loop before the publish
  stage and every publisher, wrangler, the feed/archive and
  ``publish_dir`` are unreachable. A dry run still writes what a real run
  writes before that boundary — collected items (idempotent) and the
  compose sidecar the real run overwrites — plus its log; that is the
  honest guarantee (a preview that sees different candidates than the
  run would be worse).
- **Result-size etiquette** (ticket 13 §4): lists return
  ``{items, total, truncated}`` summaries (default limit 50, hard max
  200); score previews cap at ``MAX_LLM_ITEMS`` with 200-char reasons and
  ``llm_error`` always present; run reports are summarized to per-stage
  ``{type, plugin, outcome}`` plus counts and the select's
  ``{ids, totals}`` unless ``verbose``; the sidecar path is returned
  instead of the bulk data. Failures propagate as raised exceptions with
  human-written messages — each adapter contains them into its own error
  shape; no tool returns a stack trace.

Deliberately absent: CLI parity (the CLI stays the human/ops surface),
station rename/delete (immutability *is* the API surface — the repo
exposes no such operation), and rubric-ref resolution at save time (a
workflow may pin a rubric created later in the same conversation; the run
pre-flight resolves loudly).
"""

from __future__ import annotations

from typing import Any, Callable

from ..llm.base import get_adapter
from ..morning.script import sidecar_path
from ..pipeline.digest import (MAX_LLM_ITEMS, candidate_items,
                               classify_verdicts, technical_only)
from ..workflow import engine as _engine
from ..workflow.catalog import (CatalogError, WorkflowCatalog,
                                ensure_default_catalog, parse_ref)
from ..workflow.rubric import (RubricError, require_valid_rubric,
                               publisher_counts, rubric_ref, score_items,
                               story_key)
from ..workflow.rubric_catalog import RubricCatalog, \
    ensure_default_rubric_catalog
from ..workflow.schema import require_valid
from ..workflow.stations import FEED_IDENTITY_KEYS, StationRepo, \
    resolve_identity, station_scope

# -- size etiquette (ticket 13 §4) ------------------------------------------

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
REASON_CHARS = 200
DEFAULT_PREVIEW_LIMIT = 10

_FEED_KEYS = frozenset(FEED_IDENTITY_KEYS)


def _bounded(limit: Any, default: int, maximum: int = MAX_LIST_LIMIT) -> int:
    """A list limit inside the etiquette band (never trust the caller)."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


def _envelope(items: list[dict[str, Any]], total: int,
              limit: int) -> dict[str, Any]:
    shown = items[:limit]
    return {"items": shown, "total": total, "truncated": total > len(shown)}


def _clip(reason: Any) -> str:
    text = str(reason or "")
    return text[:REASON_CHARS]


# -- the registry: spec metadata carried by the implementation ---------------

TOOL_REGISTRY: dict[str, dict[str, Any]] = {}


def tool(name: str, description: str, args: dict[str, str], *,
         mutating: bool = False, http: str) -> Callable:
    """Register one implementation *with* its spec: a tool cannot exist
    without its description, and ``TOOL_SPECS`` is generated from here."""

    def register(fn: Callable) -> Callable:
        if name in TOOL_REGISTRY:
            raise ValueError(f"duplicate tool '{name}'")
        TOOL_REGISTRY[name] = {
            "fn": fn,
            "spec": {"name": name, "description": description,
                     "args": args, "status": "wired", "http": http,
                     "mutating": mutating},
        }
        return fn

    return register


def workflow_tool_specs() -> list[dict[str, Any]]:
    """The registry's TOOL_SPECS entries, in registration order."""
    return [entry["spec"] for entry in TOOL_REGISTRY.values()]


# -- workflows ----------------------------------------------------------------


def _catalog(session) -> WorkflowCatalog:
    ensure_default_catalog(session)  # the shipped default, like the CLI
    return WorkflowCatalog(session)


def _retired_map(catalog: Any) -> dict[str, Any]:
    """name -> retired_at for one catalog (both catalogs' ``list`` rows
    share the shape)."""
    return {row["name"]: row["retired_at"] for row in catalog.list()}


def _stage_labels(doc: dict[str, Any]) -> list[str]:
    return [s.get("name") or s["type"] for s in doc.get("stages") or []]


@tool(
    "list_workflows",
    "List catalog workflows with their latest version and stage names; "
    "summaries only — get_workflow drills into a document.",
    {"query": "string (optional; case-insensitive match on name, stage "
              "and plugin names)",
     "include_retired": "boolean (optional, default false)",
     "limit": "int (optional, default 50, max 200)"},
    http="GET /tools/list_workflows",
)
def list_workflows(session, settings, query: str | None = None,
                   include_retired: bool = False,
                   limit: int = DEFAULT_LIST_LIMIT) -> dict[str, Any]:
    catalog = _catalog(session)
    limit = _bounded(limit, DEFAULT_LIST_LIMIT)
    needle = (query or "").lower()
    items: list[dict[str, Any]] = []
    for row in catalog.list():
        if not include_retired and row["retired_at"]:
            continue
        doc = catalog.get(row["name"], row["latest"]) \
            if row["latest"] is not None else None
        plugins = [s.get("plugin") for s in (doc or {}).get("stages", [])
                   if s.get("plugin")]
        hay = " ".join([row["name"], *_stage_labels(doc or {}), *plugins])
        if needle and needle not in hay.lower():
            continue
        items.append({"name": row["name"], "latest": row["latest"],
                      "retired_at": row["retired_at"],
                      "stages": _stage_labels(doc or {}),
                      "created_at": row["created_at"]})
    return _envelope(items, len(items), limit)


def _resolve_get(ref: str, version: int | None, *, kind: str) -> str:
    """The inspection ref for a ``ref`` + optional ``version`` pair —
    both given and disagreeing is loud, never a silent pick."""
    name, pinned = parse_ref(ref, kind=kind)
    if version is not None:
        if pinned is not None and pinned != version:
            raise CatalogError(
                f"{kind} ref '{ref}' already pins version {pinned}; "
                f"the version argument says {version}")
        return f"{name}@{version}"
    return ref


@tool(
    "get_workflow",
    "Fetch one stored workflow document (whole, validated) by ref — name, "
    "name@latest, or pinned name@N; inspection works on retired names.",
    {"ref": "string (name | name@latest | name@N)",
     "version": "int (optional; the pin, if not already in ref)"},
    http="GET /tools/get_workflow",
)
def get_workflow(session, settings, ref: str,
                 version: int | None = None) -> dict[str, Any]:
    catalog = _catalog(session)
    doc = catalog.document(_resolve_get(ref, version, kind="workflow"))
    retired = _retired_map(catalog).get(doc["name"])
    return {**doc, "ref": ref, "resolved_version": doc["version"],
            "retired": retired is not None}


@tool(
    "create_workflow",
    "Append a new workflow version from a whole descriptor document. "
    "Validated on save (schema, plugin bindings, size); the next version "
    "must be exactly max+1. dry_run=true first runs the candidate once as "
    "a real dry run against the current database and refuses the save on "
    "any stage failure — opt-in because it collects (network + tokens).",
    {"document": "object (the whole v1 descriptor)",
     "dry_run": "boolean (optional, default false; the smoke test)"},
    mutating=True,
    http="POST /tools/create_workflow",
)
def create_workflow(session, settings, document: dict[str, Any],
                    dry_run: bool = False, *, via: str) -> dict[str, Any]:
    require_valid(document)  # fail fast, before any collection
    if dry_run:
        try:
            _engine.run_workflow(session, settings, document, dry_run=True)
        except _engine.WorkflowRunError as exc:
            raise CatalogError(
                f"smoke dry-run failed before save: {exc}") from exc
    version = _catalog(session).create_version(document, actor="agent",
                                               via=via)
    validation: dict[str, Any] = {"schema": True, "bindings": True}
    if dry_run:
        validation["smoke"] = True
    return {"name": document["name"], "version": version,
            "actor": "agent", "via": via, "validation": validation}


@tool(
    "diff_workflow",
    "Structural diff of two stored versions of one workflow (RFC 6901 "
    "paths; stages keyed by name, any reorder as one /stages order "
    "entry).",
    {"name": "string (workflow name)",
     "from_version": "int (base version)",
     "to_version": "int (compared version)"},
    http="POST /tools/diff_workflow",
)
def diff_workflow(session, settings, name: str, from_version: int,
                  to_version: int) -> dict[str, Any]:
    changes = _catalog(session).diff(name, from_version, to_version)
    return {"name": name, "from": from_version, "to": to_version,
            "changes": changes}


def _set_catalog_retired(catalog: Any, name: str, *, retire: bool,
                         via: str) -> dict[str, Any]:
    """One retire/unretire shape for both catalogs: log the mutation
    (actor="agent", the adapter's via) and return the name's new state
    plus its origin, so a harness can verify what it just did."""
    if retire:
        catalog.retire(name, actor="agent", via=via)
    else:
        catalog.unretire(name, actor="agent", via=via)
    return {"name": name,
            "retired_at": _retired_map(catalog).get(name),
            "actor": "agent", "via": via}


@tool(
    "retire_workflow",
    "Retire a workflow name: new versions and runs are refused loudly, "
    "history stays. Logged; not a delete.",
    {"name": "string (workflow name)"},
    mutating=True,
    http="POST /tools/retire_workflow",
)
def retire_workflow(session, settings, name: str, *, via: str) \
        -> dict[str, Any]:
    return _set_catalog_retired(_catalog(session), name, retire=True, via=via)


@tool(
    "unretire_workflow",
    "Undo a workflow retirement (the data was never gone). Logged.",
    {"name": "string (workflow name)"},
    mutating=True,
    http="POST /tools/unretire_workflow",
)
def unretire_workflow(session, settings, name: str, *, via: str) \
        -> dict[str, Any]:
    return _set_catalog_retired(_catalog(session), name, retire=False, via=via)


# -- rubrics --------------------------------------------------------------------


def _rubric_catalog(session) -> RubricCatalog:
    # the shipped default rubric bootstraps once here too — the agent
    # tools are the rubric's only management surface (ticket 09 left the
    # CLI/seed to exactly this ticket), so `default` must resolve.
    ensure_default_rubric_catalog(session)
    return RubricCatalog(session)


@tool(
    "list_rubrics",
    "List catalog rubrics with their latest version and dimension names; "
    "summaries only — get_rubric drills into a document.",
    {"limit": "int (optional, default 50, max 200)"},
    http="GET /tools/list_rubrics",
)
def list_rubrics(session, settings,
                 limit: int = DEFAULT_LIST_LIMIT) -> dict[str, Any]:
    catalog = _rubric_catalog(session)
    limit = _bounded(limit, DEFAULT_LIST_LIMIT)
    items: list[dict[str, Any]] = []
    for row in catalog.list():
        doc = catalog.get(row["name"], row["latest"]) \
            if row["latest"] is not None else None
        items.append({"name": row["name"], "latest": row["latest"],
                      "retired_at": row["retired_at"],
                      "dimensions": [d["name"] for d in
                                     (doc or {}).get("dimensions", [])],
                      "created_at": row["created_at"]})
    return _envelope(items, len(items), limit)


@tool(
    "get_rubric",
    "Fetch one stored rubric document (whole, validated) by ref; "
    "inspection works on retired names.",
    {"ref": "string (name | name@latest | name@N)",
     "version": "int (optional; the pin, if not already in ref)"},
    http="GET /tools/get_rubric",
)
def get_rubric(session, settings, ref: str,
               version: int | None = None) -> dict[str, Any]:
    catalog = _rubric_catalog(session)
    doc = catalog.document(_resolve_get(ref, version, kind="rubric"))
    retired = _retired_map(catalog).get(doc["name"])
    return {**doc, "ref": ref, "resolved_version": doc["version"],
            "retired": retired is not None}


@tool(
    "create_rubric",
    "Append a new rubric version from a whole rubric document; validated "
    "on save, next version must be exactly max+1. (No smoke: score_preview "
    "is how you try a rubric, and it writes nothing.)",
    {"document": "object (the whole v1 rubric)"},
    mutating=True,
    http="POST /tools/create_rubric",
)
def create_rubric(session, settings, document: dict[str, Any], *,
                  via: str) -> dict[str, Any]:
    version = _rubric_catalog(session).create_version(document,
                                                      actor="agent",
                                                      via=via)
    return {"name": document["name"], "version": version,
            "actor": "agent", "via": via}


@tool(
    "diff_rubric",
    "Structural diff of two stored versions of one rubric (RFC 6901 "
    "paths; dimensions keyed by name, any reorder as one /dimensions "
    "order entry — order is behavior, weights renormalize over the "
    "sequence).",
    {"name": "string (rubric name)",
     "from_version": "int (base version)",
     "to_version": "int (compared version)"},
    http="POST /tools/diff_rubric",
)
def diff_rubric(session, settings, name: str, from_version: int,
                to_version: int) -> dict[str, Any]:
    changes = _rubric_catalog(session).diff(name, from_version, to_version)
    return {"name": name, "from": from_version, "to": to_version,
            "changes": changes}


@tool(
    "score_preview",
    "Score the CURRENT candidate set against a rubric — a stored ref or "
    "an inline draft document — with the same window/cap/filter/rank the "
    "select strategies use. Writes nothing: no collection, no catalog row. "
    "Returns per-item totals plus stored per-dimension reasons (ADR 0001) "
    "and llm_error (null when the model pass ran). The elicitation loop's "
    "core: draft, preview, adjust.",
    {"rubric": "string (ref) or object (inline draft document)",
     "limit": "int (optional, default 10, max 30)",
     "hours": "number (optional, default 24; the selection window)"},
    http="POST /tools/score_preview",
)
def score_preview(session, settings, rubric: Any, limit: int =
                  DEFAULT_PREVIEW_LIMIT, hours: float = 24) \
        -> dict[str, Any]:
    if not isinstance(hours, (int, float)) or isinstance(hours, bool) \
            or hours <= 0:
        raise CatalogError(f"hours must be a number > 0, got {hours!r}")
    if isinstance(rubric, str):
        rubric_doc = _rubric_catalog(session).document(rubric)
    else:
        rubric_doc = rubric
        try:
            require_valid_rubric(rubric_doc)
        except RubricError as exc:
            raise CatalogError(str(exc)) from exc
    limit = _bounded(limit, DEFAULT_PREVIEW_LIMIT, maximum=MAX_LLM_ITEMS)

    candidates, window = candidate_items(session, hours=hours)
    adapter = get_adapter(settings)
    verdicts, verdict_method = classify_verdicts(adapter, candidates)
    filtered = technical_only(candidates, verdicts)
    scoring = score_items(rubric_doc, filtered, adapter, window_hours=hours)
    titles = {i["id"]: i.get("title") for i in filtered}
    outlets = publisher_counts(filtered)
    preview = []
    for s in scoring["scores"][:limit]:
        key = story_key(titles.get(s["id"]))
        preview.append({"id": s["id"], "total": s["total"],
                        "outlets": outlets.get(key) if key else None,
                        "dimensions": {name: {"score": dim["score"],
                                              "reason": _clip(dim["reason"])}
                                       for name, dim in
                                       s["dimensions"].items()}})
    return {
        "rubric_ref": rubric_ref(rubric_doc),
        "method": scoring["method"],
        "llm_error": scoring.get("llm_error"),
        "window_hours": hours,
        "items_in_window": window,
        "considered": len(filtered),
        "verdict_method": verdict_method,
        "scores": preview,
    }


@tool(
    "retire_rubric",
    "Retire a rubric name: new versions and runs are refused loudly, "
    "history stays. Logged; not a delete.",
    {"name": "string (rubric name)"},
    mutating=True,
    http="POST /tools/retire_rubric",
)
def retire_rubric(session, settings, name: str, *, via: str) \
        -> dict[str, Any]:
    return _set_catalog_retired(_rubric_catalog(session), name,
                                retire=True, via=via)


@tool(
    "unretire_rubric",
    "Undo a rubric retirement (the data was never gone). Logged.",
    {"name": "string (rubric name)"},
    mutating=True,
    http="POST /tools/unretire_rubric",
)
def unretire_rubric(session, settings, name: str, *, via: str) \
        -> dict[str, Any]:
    return _set_catalog_retired(_rubric_catalog(session), name,
                                retire=False, via=via)


# -- stations ---------------------------------------------------------------------


def _station_repo(session) -> StationRepo:
    return StationRepo(session)


def _station_or_loud(repo: StationRepo, name: str) -> dict[str, Any]:
    row = repo.get(name)
    if row is None:
        raise CatalogError(f"unknown station '{name}'")
    return row


@tool(
    "list_stations",
    "List stations with their workflow ref, watchlist binding and publish "
    "path. Retired stations are included by default: their published "
    "archives stay live.",
    {"include_retired": "boolean (optional, default true)"},
    http="GET /tools/list_stations",
)
def list_stations(session, settings, include_retired: bool = True) \
        -> dict[str, Any]:
    rows = _station_repo(session).list()
    if not include_retired:
        rows = [r for r in rows if r["retired_at"] is None]
    items = [{"name": r["name"], "workflow_ref": r["workflow_ref"],
              "watchlist_id": r["watchlist_id"],
              "path_segment": r["path_segment"],
              "retired_at": r["retired_at"]} for r in rows]
    return {"items": items, "total": len(items), "truncated": False}


@tool(
    "get_station",
    "Fetch one station: the stored row plus its resolved feed identity "
    "(NULLs fall back to the feed constants) and its watchlist scope "
    "summary.",
    {"name": "string (station name)"},
    http="GET /tools/get_station",
)
def get_station(session, settings, name: str) -> dict[str, Any]:
    repo = _station_repo(session)
    row = _station_or_loud(repo, name)
    scope = station_scope(session, row["watchlist_id"])
    return {**row, "feed_identity": resolve_identity(row),
            "scope": {"terms": sorted({t for t, _w
                                       in scope["include_terms"]}),
                      "exclude_terms": scope["exclude_terms"],
                      "source_ids": scope["source_ids"]}}


def _feed_kwargs(feed: dict[str, Any] | None) -> dict[str, Any]:
    """The feed-identity dict → repo kwargs; unknown keys are loud (the
    closed key set is the API surface, ticket 13)."""
    if feed is None:
        return {}
    unknown = sorted(set(feed) - _FEED_KEYS)
    if unknown:
        raise CatalogError(f"feed: unknown keys {', '.join(unknown)} "
                           f"(known: {', '.join(sorted(_FEED_KEYS))})")
    return {f"feed_{key}": feed.get(key) for key in FEED_IDENTITY_KEYS}


@tool(
    "create_station",
    "Register one station: name (slug), workflow ref (float or pinned), "
    "optional watchlist binding (NULL = the global term union), optional "
    "path segment (immutable once set; NULL = the legacy root) and feed "
    "identity metadata.",
    {"name": "string (slug; the GUID prefix)",
     "workflow": "string (workflow ref: name or name@N)",
     "watchlist": "int (optional watchlist id)",
     "path_segment": "string (optional URL path segment)",
     "description": "string (optional)",
     "feed": "object (optional; keys title, description, author, "
             "category, language, owner_email)"},
    mutating=True,
    http="POST /tools/create_station",
)
def create_station(session, settings, name: str, workflow: str,
                   watchlist: int | None = None,
                   path_segment: str | None = None,
                   description: str | None = None,
                   feed: dict[str, Any] | None = None, *, via: str) \
        -> dict[str, Any]:
    row = _station_repo(session).create(
        name, actor="agent", via=via, description=description,
        workflow_ref=workflow, watchlist_id=watchlist,
        path_segment=path_segment, **_feed_kwargs(feed))
    return {**row, "actor": "agent", "via": via}


@tool(
    "update_station",
    "Replace one station's mutable metadata — full-document semantics: "
    "the whole mutable set is sent, and omitted fields clear (the seed and "
    "the tools carry whole documents). name and path_segment are not "
    "parameters: they are immutable (GUIDs and enclosure URLs are "
    "permanent), and there is no delete — retirement is the lifecycle.",
    {"name": "string (station name)",
     "workflow": "string (workflow ref: name or name@N)",
     "watchlist": "int (optional watchlist id; null clears the binding)",
     "description": "string (optional)",
     "feed": "object (optional; keys title, description, author, "
             "category, language, owner_email)"},
    mutating=True,
    http="POST /tools/update_station",
)
def update_station(session, settings, name: str, workflow: str,
                   watchlist: int | None = None,
                   description: str | None = None,
                   feed: dict[str, Any] | None = None, *, via: str) \
        -> dict[str, Any]:
    row = _station_repo(session).update_identity(
        name, actor="agent", via=via, description=description,
        workflow_ref=workflow, watchlist_id=watchlist,
        **_feed_kwargs(feed))
    return {**row, "actor": "agent", "via": via}


def _set_station_retired(repo: StationRepo, name: str, *, retire: bool,
                         via: str) -> dict[str, Any]:
    if retire:
        repo.retire(name, actor="agent", via=via)
    else:
        repo.unretire(name, actor="agent", via=via)
    return {"name": name, "retired_at": repo.get(name)["retired_at"],
            "actor": "agent", "via": via}


@tool(
    "retire_station",
    "Retire a station: its runs are refused loudly from now on; the "
    "published archive stays live. Logged; not a delete.",
    {"name": "string (station name)"},
    mutating=True,
    http="POST /tools/retire_station",
)
def retire_station(session, settings, name: str, *, via: str) \
        -> dict[str, Any]:
    return _set_station_retired(_station_repo(session), name,
                                retire=True, via=via)


@tool(
    "unretire_station",
    "Undo a station retirement (the row was never gone). Logged.",
    {"name": "string (station name)"},
    mutating=True,
    http="POST /tools/unretire_station",
)
def unretire_station(session, settings, name: str, *, via: str) \
        -> dict[str, Any]:
    return _set_station_retired(_station_repo(session), name,
                                retire=False, via=via)


# -- runs -----------------------------------------------------------------------

# the count fields a summarized stage keeps (ticket 13 §3: "counts, not
# payloads"); everything else lives in the sidecar and the log.
_STAGE_SUMMARY_KEYS = ("sources", "status", "method", "items_in_window",
                       "candidates", "after_filter", "in_pack", "words",
                       "est_seconds", "chunks", "duration_seconds",
                       "engine", "publisher", "outcome")


def _summarize_report(report: dict[str, Any],
                      descriptor: dict[str, Any], settings: Any) \
        -> dict[str, Any]:
    """The run report as a receipt: per stage ``{type, plugin, outcome}``
    plus counts and the select's ``{ids, totals}`` — bulk data stays in
    the sidecar (whose path the summary carries) and the log."""
    by_stage = {(s.get("name") or s["type"]): s
                for s in descriptor["stages"]}
    summary: dict[str, Any] = {
        "date": report["date"], "outcome": report["outcome"],
        "workflow": {"name": descriptor["name"],
                     "version": descriptor["version"]}}
    if report.get("station"):
        summary["station"] = report["station"]
    stages: dict[str, Any] = {}
    for name, payload in report["stages"].items():
        spec = by_stage.get(name, {})
        entry: dict[str, Any] = {"type": spec.get("type", name)}
        if spec.get("plugin"):
            entry["plugin"] = spec["plugin"]
        for key in _STAGE_SUMMARY_KEYS:
            if key in payload:
                entry[key] = payload[key]
        if "scores" in payload:  # the select's scoring, ids + totals only
            entry["ids"] = [s["id"] for s in payload["scores"]]
            entry["totals"] = {s["id"]: s["total"]
                               for s in payload["scores"]}
        stages[name] = entry
    summary["stages"] = stages
    if stages:  # the sidecar the real run overwrites (ticket 13 §3)
        summary["sidecar"] = str(sidecar_path(settings, report["date"],
                                              report.get("station")))
    return summary


@tool(
    "run_workflow",
    "Run a catalog workflow by ref as a DRY RUN and return the run "
    "report. There is no publish parameter: the tool cannot publish — "
    "the stage loop stops before the publish stage, so publishers, "
    "wrangler, the feed and the archive are unreachable. Writes only "
    "what a dry run always writes before that boundary: collected items "
    "(idempotent), the compose sidecar the real run overwrites, and the "
    "log. Publishing outward-facing content stays the schedule's job — "
    "ask the owner to dispatch it.",
    {"ref": "string (workflow ref: name, name@latest, or name@N)",
     "station": "string (optional station name)",
     "date": "string (optional YYYY-MM-DD; default today in the "
             "morning timezone)",
     "verbose": "boolean (optional, default false; return the full "
                "stage payloads)"},
    http="POST /tools/run_workflow",
)
def run_workflow(session, settings, ref: str, station: str | None = None,
                 date: str | None = None, verbose: bool = False) \
        -> dict[str, Any]:
    ensure_default_catalog(session)  # the shipped default, like the CLI
    descriptor = _catalog(session).resolve(ref)
    report = _engine.run_workflow(session, settings, descriptor, station,
                                  date=date, dry_run=True)
    if verbose:
        return {**report,
                "sidecar": str(sidecar_path(settings, report["date"],
                                            report.get("station")))}
    return _summarize_report(report, descriptor, settings)


__all__ = ["TOOL_REGISTRY", "workflow_tool_specs",
           "DEFAULT_LIST_LIMIT", "MAX_LIST_LIMIT",
           "DEFAULT_PREVIEW_LIMIT", "REASON_CHARS"] + \
          [name for name in TOOL_REGISTRY]
