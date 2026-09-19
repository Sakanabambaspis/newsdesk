"""The workflow engine and RunContext contract (wayfinder ticket 02).

The engine is the second deep interface of the workflow architecture: one
call — ``run_workflow(session, settings, descriptor, station, date)`` —
hiding dispatch, guardrail enforcement, the bounded repair loop, logging
and error aggregation. Everything below is the written contract; the W1
build (ticket 05) routes ``newsdesk morning`` through it. Decisions:

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
  invariant over the channel). Plugins keep their v1 signatures;
  per-type adapters translate context → plugin, and the context-native
  plugin convention arrives with registration metadata (ticket 05+).

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
  the emission guard declared, named and validated in *data*. v1
  implements the three the shipped descriptor carries; the W3 names
  fail loudly as unimplemented if ever encountered — never a silent
  pass.

- **Repair loop** (``on_fail: repair``): the failing stage re-runs with
  the violation notes on the context, at most ``loop_policy.max_attempts``
  times (schema-bounded 1..2, default 2), then one contained-degrade
  re-run (the plugin chooses its degrade; context-native plugins read
  ``ctx.degrade``), then a loud stage-tagged failure — at most
  ``1 + max_attempts + 1`` invocations. ``on_fail`` is per *check*: only
  a violating ``fail`` check aborts; a repairable violation repairs even
  with a fatal sibling. v1 honesty: today's plugins ignore the notes, so
  their re-runs are identical — the bound still caps a misbehaving
  plugin's blast radius, and ticket 05's context-native plugins read
  ``ctx.violations`` / ``ctx.degrade`` to actually vary their output.

- **Log events**: the engine appends uniform ``workflow_stage_started /
  finished / failed`` entries and terminates runs with
  ``workflow_run_finished / workflow_run_failed`` — supersets of today's
  ``morning_run_finished / failed`` shapes. The morning names retire when
  ticket 05 routes the CLI through the engine (two engines must not use
  different names for the same run-level fact); domain entries emitted by
  stages (``daily_digest_built``, ``morning_brief_built``, ...) are
  unchanged.

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
from .schema import STAGE_TYPES, require_valid, workflow_descriptor_path


class WorkflowRunError(Exception):
    """A workflow stage failed; message is safe to print (no secrets)."""

    def __init__(self, message: str, stage: str):
        super().__init__(message)
        self.stage = stage


class _ChecksFailed(Exception):
    """Internal: deterministic checks failed; message lists violations."""


class RunContext:
    """One run's state on the one first-class channel (ticket 02).

    The engine owns this object; stage adapters and (once context-native,
    ticket 05+) plugins read from it and never from globals.

    Attributes:
        artifacts: the artifact bus, keyed by the stage-type triples'
            artifact names (``collection, digest, script, audio, episode``).
        reports: per-stage payloads keyed by stage name; becomes the run
            report's ``stages`` object.
        violations: repair bookkeeping — the failed-check messages of the
            current attempt, per stage name, for context-native plugins.
        degrade: stage names running their contained-degrade pass (the
            last bounded re-run after repairs are exhausted).
        plugins: pre-resolved registry plugins, keyed by stage type; the
            publish entry is ``(resolved_name, plugin)`` for the log.
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

    def log(self, action: str, detail: dict[str, Any]) -> None:
        LogRepo(self.session).append(action, detail)


def load_descriptor(name: str, version: int) -> dict[str, Any]:
    """Load a shipped package descriptor ``name@version``, validated."""
    path = workflow_descriptor_path(name, version)
    doc = json.loads(path.read_text(encoding="utf-8"))
    require_valid(doc)
    return doc


def validate_registration(stage_type: str, requires: tuple[str, ...],
                          provides: tuple[str, ...]) -> list[str]:
    """Check a plugin's declared triple against its stage type.

    Ticket 01 put per-plugin requires/provides metadata at registration;
    the mechanism wires up at ticket 05, but the rule is fixed here: a
    plugin may narrow its type's triple, never contradict it.
    """
    triple = STAGE_TYPES.get(stage_type)
    if triple is None:
        return [f"unknown stage type '{stage_type}' "
                f"(known: {', '.join(STAGE_TYPES)})"]
    errors = [f"plugin requires '{key}' which stage type '{stage_type}' "
              f"does not ({', '.join(triple['requires'])})"
              for key in requires if key not in triple["requires"]]
    errors.extend(f"plugin provides '{key}' which stage type "
                  f"'{stage_type}' does not ({', '.join(triple['provides'])})"
                  for key in provides if key not in triple["provides"])
    return errors


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


# name -> (binding phase, implementation). "pre" validates before the
# stage dispatches; "post" validates the artifact the stage provided.
_CHECKS: dict[str, tuple[str, Callable[..., list[str]]]] = {
    "section_allowlist": ("post", _check_section_allowlist),
    "word_budget": ("post", _check_word_budget),
    "archive_intact": ("pre", _check_archive_intact),
}


def _run_checks(ctx: RunContext, spec: dict[str, Any],
                phase: str) -> list[tuple[str, bool]]:
    """Violations of one phase's checks as ``(message, fatal)`` pairs —
    ``on_fail`` is per *check*: ``fail`` is loud, ``repair`` may re-run."""
    found: list[tuple[str, bool]] = []
    for check in spec.get("checks") or []:
        name = check["name"]
        entry = _CHECKS.get(name)
        if entry is None:
            found.append((f"check '{name}' is not implemented "
                          f"(lands with W3)", True))
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
    brief = stage_script(ctx.session, ctx.settings, ctx.artifacts["digest"],
                         ctx.date, writer=ctx.plugins["compose"])
    ctx.artifacts["script"] = brief
    return {"method": brief["method"], **brief["stats"]}


def _stage_render(ctx: RunContext, spec: dict[str, Any]) -> dict[str, Any]:
    audio = ctx.plugins["render"](
        ctx.settings, Path(ctx.artifacts["script"]["sidecar"]))
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
        ctx.plugins["render"] = TTS_ENGINES.get(
            pinned.get("render") or ctx.settings.morning_tts)
    if any(s["type"] == "publish" for s in ctx.workflow["stages"]):
        name = pinned.get("publish") or ctx.settings.morning_publisher
        ctx.plugins["publish"] = (name, PUBLISHERS.get(name))
    if any(s["type"] == "compose" for s in ctx.workflow["stages"]):
        ctx.plugins["compose"] = SCRIPTWRITERS.get(pinned.get("compose"))


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
