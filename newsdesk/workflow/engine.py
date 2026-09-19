"""The workflow engine and RunContext contract (wayfinder ticket 02).

The engine is the second deep interface of the workflow architecture: one
call — ``run_workflow(session, settings, descriptor, station, date)`` —
hiding dispatch, guardrail enforcement, the bounded repair loop, logging
and error aggregation. The W1 build (ticket 05) routes ``newsdesk
morning`` through it. Decisions:

- **RunReport** is the plain JSON-shaped dict ``newsdesk morning`` prints
  today: ``{date, outcome, stages: {...}, finished_at}``; outcomes are
  ``published``, ``already_published``, ``dry_run``. Stage keys are the
  descriptor's stage *names* (a stage defaults to its type), so
  ``default-morning@1`` names its renamed stages with today's observable
  keys — ``digest``/``script``/``tts`` — and the report JSON, the CLI's
  per-stage prints and the characterization pins survive unchanged.
  Rejected: a rename map inside the engine (select→digest) — that hides a
  vocabulary migration the data can carry honestly.

- **RunContext** is the one first-class channel (Cordis context
  paradigm): session, settings, the validated descriptor, station, run
  date, the artifact bus, per-stage reports, repair bookkeeping and the
  log all live on it; no run state in globals. Every stage reads its
  required artifacts from the bus by key and its result is placed under
  its type's ``provides`` key; ``workflow_stage_finished`` records the
  keys the stage saw ("artifact-visible means bus-keyed" — the runtime
  invariant over the channel). Plugins keep their bare signatures;
  per-type adapters translate context → plugin, and plugins registered
  with ``context=True`` receive the RunContext itself (registration
  metadata, wired at ticket 05).

- **Dispatch** is per stage type: collect/select run engine built-ins
  (the only implementations — registries arrive with W3's select
  strategies); compose/render/publish resolve through the morning
  registries — a pinned ``plugin`` key wins, else the v1 unpinned rule
  (per-type settings knob: ``morning_tts`` / ``morning_publisher``; else
  the registry default); notify fans out over every registered notifier
  (zero = the designed no-op). All registry plugins resolve before the
  first stage runs, so a bad selection does nothing at all — a resolution
  failure is stage-tagged ``selection``, today's pre-flight tag.

- **Checks** are the named deterministic library, keyed by check name;
  each name has a fixed binding phase: pre-stage (``archive_intact``)
  validates before its stage dispatches; post-stage checks validate the
  provided artifact. Pre-stage checks do not repair (nothing has run
  yet), whatever their ``on_fail`` says. ``archive_intact`` consults the
  same publish-plugin guard as the run-level idempotency hook below, so
  on the default chain the hook always wins the race and the check
  cannot fire — ticket 01 recorded this; the check's role is keeping
  the emission guard declared, named and validated in *data*. All six
  names are implemented (the coverage pair ``distinct_stories`` /
  ``diversity_floor`` guard the episode against the 2026-09-19 failure —
  the floor scales to what the pack offered, so a quiet day never
  fails); a check whose artifacts are not on the bus violates with a
  binding hint — never a silent pass.

- **Repair loop** (``on_fail: repair``): the failing stage re-runs with
  the violation notes on the context, at most ``loop_policy.max_attempts``
  times (schema-bounded 1..2, default 2), then one contained-degrade
  re-run (the plugin chooses its degrade; context-native plugins read
  ``ctx.degrade``), then a loud stage-tagged failure — at most
  ``1 + max_attempts + 1`` invocations. ``on_fail`` is per *check*: only
  a violating ``fail`` check aborts; a repairable violation repairs even
  with a fatal sibling. Plugins registered with ``context=True`` (the
  context-native convention, wired at ticket 05) receive the context and
  read ``ctx.violations`` / ``ctx.degrade`` — ``llm-brief`` skips its LLM
  call on the degrade pass; the other built-ins ignore the notes, and the
  bound still caps a misbehaving plugin's blast radius.

- **Log events**: the engine appends uniform ``workflow_stage_started /
  finished / failed`` entries and terminates runs with
  ``workflow_run_finished / workflow_run_failed`` — supersets of the old
  ``morning_run_*`` shapes. The morning names retired when the CLI
  routed through the engine (ticket 05); ``run_morning`` remains only as
  the characterization specimen, so two live engines never use different
  names for the same run-level fact. Domain entries emitted by stages
  (``daily_digest_built``, ``morning_brief_built``, ...) are unchanged.

- **Idempotency** hooks before the first stage: the publish plugin's
  ``already_published`` guard for (station, date) short-circuits the run
  with outcome ``already_published`` and no work — today's guard, same
  placement, same report shape. Dry-run skips the guard and stops before
  the first ``publish`` stage: publish/notify-free, though derived
  sidecar/audio files still land under the date (the real run
  overwrites them).

- **Behavior preservation**, defined: for equal database state, settings
  and date, an engine run of ``default-morning@1`` and today's
  ``run_morning`` produce identical published artifacts (sidecar, audio,
  manifest, feed), identical report JSON modulo ``finished_at``,
  identical domain log entries in order, and run-level entries equal on
  every field they share; the engine adds only its uniform stage events.
  Pinned by ``tests/test_workflow_engine.py``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, NoReturn

from ..morning.orchestrator import _notify, stage_script
from ..morning.registries import (PUBLISHERS, SCRIPTWRITERS, TTS_ENGINES,
                                  load_plugins)
from ..morning.script import episode_date, word_count
from ..pipeline.digest import build_daily_digest
from ..pipeline.runner import run_collection
from ..storage.repo import LogRepo
from .rubric import story_key
from .schema import STAGE_TYPES, require_valid, validate_registration, \
    workflow_descriptor_path


class WorkflowRunError(Exception):
    """A workflow stage failed; message is safe to print (no secrets)."""

    def __init__(self, message: str, stage: str):
        super().__init__(message)
        self.stage = stage


class _ChecksFailed(Exception):
    """Internal: deterministic checks failed; message lists violations."""


class RunContext:
    """One run's state on the one first-class channel (ticket 02).

    The engine owns this object; stage adapters and context-native
    plugins read from it and never from globals.

    Attributes:
        artifacts: the artifact bus, keyed by the stage-type triples'
            artifact names (``collection, digest, script, audio, episode``).
        reports: per-stage payloads keyed by stage name; becomes the run
            report's ``stages`` object.
        violations: repair bookkeeping — the failed-check messages of the
            current attempt, per stage name, for context-native plugins.
        degrade: stage names running their contained-degrade pass (the
            last bounded re-run after repairs are exhausted).
        plugins: pre-resolved registry plugins, keyed by stage type as
            ``(resolved_name, plugin)`` for the log and metadata lookup.
        current_stage: the stage name being dispatched, for context-native
            plugins to locate their own entry in ``violations``/``degrade``.
    """

    def __init__(self, session: Any, settings: Any, workflow: dict[str, Any],
                 station: Any, date: str):
        self.session = session
        self.settings = settings
        self.workflow = workflow
        self.station = station
        self.date = date
        self.artifacts: dict[str, Any] = {}
        self.reports: dict[str, Any] = {}
        self.violations: dict[str, list[str]] = {}
        self.degrade: set[str] = set()
        self.plugins: dict[str, Any] = {}
        self.current_stage: str | None = None

    def log(self, action: str, detail: dict[str, Any]) -> None:
        LogRepo(self.session).append(action, detail)


def load_descriptor(name: str, version: int) -> dict[str, Any]:
    """Load a shipped package descriptor ``name@version``, validated."""
    path = workflow_descriptor_path(name, version)
    doc = json.loads(path.read_text(encoding="utf-8"))
    require_valid(doc)
    return doc


# -- Deterministic check library (no LLM anywhere in here) --------------------

def _check_section_allowlist(ctx: RunContext,
                             params: dict[str, Any]) -> list[str]:
    allow = set(params["allow"])
    bad = sorted({s["type"] for s in ctx.artifacts["script"]["sections"]
                  if s["type"] not in allow})
    if bad:
        return [f"sections outside the allowlist: {', '.join(bad)}"]
    return []


def _check_word_budget(ctx: RunContext,
                       params: dict[str, Any]) -> list[str]:
    brief = ctx.artifacts["script"]
    violations: list[str] = []
    per = params.get("per_section") or {}
    for section in brief["sections"]:
        cap = per.get(section["type"])
        words = word_count(section["text"])
        if cap is not None and words > cap:
            violations.append(f"section '{section['type']}' has {words} "
                              f"words (cap {cap})")
    total = params.get("total")
    if total is not None:
        words = sum(word_count(s["text"]) for s in brief["sections"])
        if words > total:
            violations.append(f"script has {words} words (cap {total})")
    return violations


def _check_archive_intact(ctx: RunContext,
                          params: dict[str, Any]) -> list[str]:
    _name, publish = ctx.plugins.get("publish", (None, None))
    guard = getattr(publish, "already_published", None)
    if guard is not None and guard(ctx.settings, ctx.date):
        return [f"episode {ctx.date} is already in the archive — publish "
                f"never overwrites or prunes it"]
    return []


def _pack_items(ctx: RunContext) -> list[dict[str, Any]]:
    """The writer's material pack (ADR 0001): what selection offered."""
    digest = ctx.artifacts.get("digest") or {}
    return ((digest.get("material_pack") or {}).get("items")) or []


def _episode_items(ctx: RunContext) -> list[dict[str, Any]]:
    """The pack items the composed episode actually covers."""
    by_id = {i["id"]: i for i in _pack_items(ctx)}
    script = ctx.artifacts.get("script") or {}
    return [by_id[iid]
            for section in script.get("sections") or []
            for iid in section.get("item_ids") or []
            if iid in by_id]


def _coverage_artifacts_missing(ctx: RunContext) -> list[str]:
    """Shared binding guard of the coverage checks: both read the digest's
    pack and the composed script, so they belong after compose."""
    if "digest" not in ctx.artifacts or "script" not in ctx.artifacts:
        return ["needs the 'digest' and 'script' artifacts on the bus "
                "(bind it after compose)"]
    return []


def _check_distinct_stories(ctx: RunContext,
                            params: dict[str, Any]) -> list[str]:
    """Cluster collapse (the 2026-09-19 fix): the episode's slots must not
    be eaten by one syndication cluster. The floor scales to what the pack
    offered — a quiet day legally covers fewer stories, never fails."""
    guard = _coverage_artifacts_missing(ctx)
    if guard:
        return guard
    pack, episode = _pack_items(ctx), _episode_items(ctx)
    offered = len({story_key(i.get("title")) for i in pack} - {""})
    covered = len({story_key(i.get("title")) for i in episode} - {""})
    floor = min(params["min_distinct"], offered)
    if covered < floor:
        return [f"episode covers {covered} distinct stor"
                f"{'y' if covered == 1 else 'ies'} (floor {floor}; the "
                f"pack offers {offered} — a syndication cluster may have "
                f"eaten the slots)"]
    return []


def _themes_of(items: list[dict[str, Any]]) -> set[str]:
    themes: set[str] = set()
    for item in items:
        themes.update(t for t in item.get("matched_terms") or [] if t)
    return themes


def _check_diversity_floor(ctx: RunContext,
                           params: dict[str, Any]) -> list[str]:
    """Cross-theme diversity floor (the 2026-09-19 fix): the episode must
    span watchlist themes in proportion to what the pack offered — the
    floor scales down to the material, never up from it."""
    guard = _coverage_artifacts_missing(ctx)
    if guard:
        return guard
    offered = _themes_of(_pack_items(ctx))
    floor = min(params["min_themes"], len(offered))
    spanned = _themes_of(_episode_items(ctx))
    if len(spanned) < floor:
        return [f"episode spans {len(spanned)} watchlist theme"
                f"{'s' if len(spanned) != 1 else ''} ({', '.join(sorted(spanned)) or 'none'};"
                f" floor {floor}; the pack offers {len(offered)}: "
                f"{', '.join(sorted(offered)) or 'none'})"]
    return []


def _check_duration_band(ctx: RunContext,
                         params: dict[str, Any]) -> list[str]:
    """The rendered episode's measured duration must sit in the band."""
    audio = ctx.artifacts.get("audio") or {}
    seconds = audio.get("duration_seconds")
    if seconds is None:
        return ["needs the 'audio' artifact on the bus (bind it after "
                "render)"]
    lo, hi = params["min_seconds"], params["max_seconds"]
    if seconds < lo:
        return [f"episode is {seconds}s (band minimum {lo}s)"]
    if seconds > hi:
        return [f"episode is {seconds}s (band maximum {hi}s)"]
    return []


# name -> (binding phase, implementation). "pre" validates before the
# stage dispatches; "post" validates the artifact the stage provided.
_CHECKS: dict[str, tuple[str, Callable[..., list[str]]]] = {
    "section_allowlist": ("post", _check_section_allowlist),
    "word_budget": ("post", _check_word_budget),
    "archive_intact": ("pre", _check_archive_intact),
    "distinct_stories": ("post", _check_distinct_stories),
    "diversity_floor": ("post", _check_diversity_floor),
    "duration_band": ("post", _check_duration_band),
}


def _run_checks(ctx: RunContext, spec: dict[str, Any],
                phase: str) -> list[tuple[str, bool]]:
    """Violations of one phase's checks as ``(message, fatal)`` pairs —
    ``on_fail`` is per *check*: ``fail`` is loud, ``repair`` may re-run."""
    found: list[tuple[str, bool]] = []
    for check in spec.get("checks") or []:
        name = check["name"]
        entry = _CHECKS.get(name)
        if entry is None:  # the schema's closed set has drifted from the
            found.append((f"check '{name}' has no implementation "
                          f"(schema/library drift)", True))
            continue
        if entry[0] != phase:
            continue
        found.extend((f"check '{name}': {v}",
                      check.get("on_fail", "repair") == "fail")
                     for v in entry[1](ctx, check.get("params") or {}))
    return found


# -- Stage adapters: the v1 plugin calling convention, translated -------------

def _stage_collect(ctx: RunContext, spec: dict[str, Any]) -> dict[str, Any]:
    job = run_collection(ctx.session, ctx.settings, None)
    ctx.artifacts["collection"] = job
    return {"sources": len(job.stats.get("sources", {})),
            "status": job.status}


def _stage_select(ctx: RunContext, spec: dict[str, Any]) -> dict[str, Any]:
    digest = build_daily_digest(
        ctx.session, ctx.settings,
        hours=(spec.get("params") or {}).get("hours", 24))
    ctx.artifacts["digest"] = digest
    return {"method": digest.get("method"),
            "items_in_window": digest.get("items_in_window"),
            "verdict_method": digest.get("verdict_method")}


def _stage_compose(ctx: RunContext, spec: dict[str, Any]) -> dict[str, Any]:
    _key, writer = ctx.plugins["compose"]
    # context-native plugins (registered with context=True) read the run's
    # repair bookkeeping; v1 plugins keep their bare signatures
    if SCRIPTWRITERS.is_context_native(_key):
        brief = stage_script(ctx.session, ctx.settings,
                             ctx.artifacts["digest"], ctx.date, writer=writer,
                             ctx=ctx)
    else:
        brief = stage_script(ctx.session, ctx.settings,
                             ctx.artifacts["digest"], ctx.date, writer=writer)
    ctx.artifacts["script"] = brief
    return {"method": brief["method"], **brief["stats"]}


def _stage_render(ctx: RunContext, spec: dict[str, Any]) -> dict[str, Any]:
    _name, render = ctx.plugins["render"]
    audio = render(ctx.settings, Path(ctx.artifacts["script"]["sidecar"]))
    ctx.log("morning_audio_rendered", {
        "date": audio["date"], "engine": audio["engine"],
        "duration_seconds": audio["duration_seconds"],
        "chunks": audio["chunks"], "mp3": audio["mp3"]})
    ctx.artifacts["audio"] = audio
    return {"engine": audio["engine"], "chunks": audio["chunks"],
            "duration_seconds": audio["duration_seconds"],
            "mp3": audio["mp3"]}


def _stage_publish(ctx: RunContext, spec: dict[str, Any]) -> dict[str, Any]:
    name, publish = ctx.plugins["publish"]
    audio = ctx.artifacts["audio"]
    published = publish(ctx.settings,
                        {"date": ctx.date,
                         "duration_seconds": audio["duration_seconds"]},
                        audio["mp3"])
    ctx.log("morning_episode_published", {
        "date": ctx.date, "publisher": name,
        "duration_seconds": audio["duration_seconds"]})
    ctx.artifacts["episode"] = published
    return {"publisher": name, **published}


def _stage_notify(ctx: RunContext, spec: dict[str, Any]) -> dict[str, Any]:
    audio, episode = ctx.artifacts["audio"], ctx.artifacts["episode"]
    return _notify(ctx.session, ctx.settings, ctx.date, {
        "date": ctx.date,
        "duration_seconds": audio["duration_seconds"],
        "episode_url": episode["episode_url"],
        "feed_url": episode["feed_url"]})


_Adapter = Callable[[RunContext, dict[str, Any]], dict[str, Any]]
_ADAPTERS: dict[str, _Adapter] = {
    "collect": _stage_collect, "select": _stage_select,
    "compose": _stage_compose, "render": _stage_render,
    "publish": _stage_publish, "notify": _stage_notify,
}


# -- The interpreter ----------------------------------------------------------

def _run_fields(ctx: RunContext) -> dict[str, Any]:
    """Identity fields every engine event carries."""
    fields: dict[str, Any] = {"date": ctx.date,
                              "workflow": ctx.workflow["name"],
                              "version": ctx.workflow["version"]}
    if ctx.station is not None:
        fields["station"] = ctx.station
    return fields


def _resolve_plugins(ctx: RunContext) -> None:
    """Pre-flight resolution (today's ``selection`` phase): every registry
    plugin resolves before any stage runs, so an unknown selection fails
    loudly having done nothing. Resolution order matches the orchestrator
    (render, then publish, then compose)."""
    pinned = {s["type"]: s["plugin"] for s in ctx.workflow["stages"]
              if s.get("plugin")}
    if any(s["type"] == "render" for s in ctx.workflow["stages"]):
        ctx.plugins["render"] = TTS_ENGINES.resolve(
            pinned.get("render") or ctx.settings.morning_tts)
    if any(s["type"] == "publish" for s in ctx.workflow["stages"]):
        ctx.plugins["publish"] = PUBLISHERS.resolve(
            pinned.get("publish") or ctx.settings.morning_publisher)
    if any(s["type"] == "compose" for s in ctx.workflow["stages"]):
        ctx.plugins["compose"] = SCRIPTWRITERS.resolve(pinned.get("compose"))
    _validate_stage_params(ctx)


# engine built-ins close their own stage-params key sets; registry plugins
# declare theirs at registration (Registry.param_keys)
_BUILTIN_PARAM_KEYS: dict[str, frozenset[str]] = {
    "collect": frozenset(), "select": frozenset({"hours"}),
}
_REGISTRY_BY_TYPE = {"compose": SCRIPTWRITERS, "render": TTS_ENGINES,
                     "publish": PUBLISHERS}


def _allowed_param_keys(ctx: RunContext, stage_type: str) -> frozenset[str]:
    if stage_type in _BUILTIN_PARAM_KEYS:
        return _BUILTIN_PARAM_KEYS[stage_type]
    entry = ctx.plugins.get(stage_type)
    if entry is None:  # notify fans out — no single plugin, no params
        return frozenset()
    return _REGISTRY_BY_TYPE[stage_type].param_keys(entry[0])


def _validate_stage_params(ctx: RunContext) -> None:
    """Registration closed each plugin's params key set; unknown keys fail
    pre-flight (witnesses at use — zero work done, tagged ``selection``)."""
    errors: list[str] = []
    for spec in ctx.workflow["stages"]:
        stype = spec["type"]
        unknown = sorted(set(spec.get("params") or {})
                         - _allowed_param_keys(ctx, stype))
        if unknown:
            errors.append(f"stage '{spec.get('name') or stype}': unknown "
                          f"params {', '.join(unknown)}")
    if errors:
        raise ValueError("; ".join(errors))


def _abort(ctx: RunContext, stage: str, exc: Exception) -> NoReturn:
    error = str(exc)[:300]
    ctx.log("workflow_stage_failed",
            {**_run_fields(ctx), "stage": stage, "error": error})
    ctx.log("workflow_run_failed",
            {**_run_fields(ctx), "stage": stage, "error": error})
    raise WorkflowRunError(f"{stage} stage failed: {exc}", stage=stage) \
        from exc


def run_workflow(session: Any, settings: Any, descriptor: dict[str, Any],
                 station: Any = None, *, date: str | None = None,
                 dry_run: bool = False) -> dict[str, Any]:
    """Run one data-defined workflow; returns the run report.

    ``descriptor`` must be a valid v1 document (validated on entry —
    witnesses at definition and at use). ``station`` is the output
    surface's identity; v1 has no Station rows yet (ticket 10), so it may
    stay ``None`` and only rides the context and log events. ``date``
    defaults to today in the morning timezone, like ``run_morning``.
    """
    require_valid(descriptor)
    load_plugins()  # idempotent: built-in plugins must be registered
    date = date or episode_date()
    ctx = RunContext(session, settings, descriptor, station, date)
    try:
        _resolve_plugins(ctx)
    except Exception as exc:
        # today's pre-flight tag: a bad selection fails having done no work
        ctx.log("workflow_run_failed",
                {**_run_fields(ctx), "stage": "selection",
                 "error": str(exc)[:300]})
        raise WorkflowRunError(f"selection stage failed: {exc}",
                               stage="selection") from exc

    _name, publish = ctx.plugins.get("publish", (None, None))
    guard = getattr(publish, "already_published", None)
    if not dry_run and guard is not None and guard(settings, date):
        ctx.log("workflow_run_finished",
                {**_run_fields(ctx), "outcome": "already_published"})
        return {"date": date, "outcome": "already_published", "stages": {}}

    max_attempts = (descriptor.get("loop_policy") or {}).get(
        "max_attempts", 2)
    for spec in descriptor["stages"]:
        stage = spec.get("name") or spec["type"]
        if dry_run and spec["type"] == "publish":
            break  # the emission boundary: publish and notify never run
        ctx.log("workflow_stage_started",
                {**_run_fields(ctx), "stage": stage})
        ctx.current_stage = stage
        try:
            pre = _run_checks(ctx, spec, "pre")
            if pre:  # pre-stage checks cannot repair; loud, immediately
                raise _ChecksFailed("; ".join(m for m, _ in pre))
            attempt = 0
            while True:
                report = _ADAPTERS[spec["type"]](ctx, spec)
                post = _run_checks(ctx, spec, "post")
                if not post:
                    break
                if any(fatal for _, fatal in post):
                    raise _ChecksFailed("; ".join(m for m, _ in post))
                if stage in ctx.degrade:
                    raise _ChecksFailed(
                        "still failing after repair and degrade: "
                        + "; ".join(m for m, _ in post))
                if attempt < max_attempts:
                    attempt += 1
                else:
                    ctx.degrade.add(stage)
                ctx.violations[stage] = [m for m, _ in post]
            ctx.reports[stage] = report
            triple = STAGE_TYPES[spec["type"]]
            ctx.log("workflow_stage_finished", {
                **_run_fields(ctx), "stage": stage, "attempt": attempt,
                "artifacts_in": list(triple["requires"]),
                "artifacts_out": list(triple["provides"])})
        except Exception as exc:
            _abort(ctx, stage, exc)

    outcome = "dry_run" if dry_run else "published"
    ctx.log("workflow_run_finished",
            {**_run_fields(ctx), "outcome": outcome})
    return {"date": date, "outcome": outcome, "stages": ctx.reports,
            "finished_at": datetime.now().isoformat(timespec="seconds")}


__all__ = ["RunContext", "WorkflowRunError", "load_descriptor",
           "run_workflow", "validate_registration"]
