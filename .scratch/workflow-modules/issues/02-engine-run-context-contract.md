# Engine & RunContext contract

Type: prototype
Status: resolved
Blocked by: 01

## Question

What is the engine's exact contract? Decide:

- `run_workflow(session, settings, descriptor, station, date) -> RunReport` — signature, report shape, dry-run mode.
- RunContext contents: the artifact bus (named keys, who provides/requires), per-stage reports, log threading — one first-class channel, no globals (Cordis context paradigm).
- Stage dispatch via the registries; between-stage check enforcement; bounded repair-loop interface (fail → targeted stage re-run with failure notes, ≤2 attempts → contained degrade → loud stage-tagged failure).
- Per-stage log events (`workflow_stage_started/finished/failed`); idempotency hook point; error aggregation (MorningError-style, stage-tagged).

Deliverable: a written contract + skeletal signatures, shown
behavior-preserving against today's `newsdesk/morning/orchestrator.py` — the
observable behavior of the default chain must be reproducible through the
engine (pin via the characterization suite from ticket 03).

## Answer

Resolved 2026-09-20. Shipped as `newsdesk/workflow/engine.py` — the written
contract is the module docstring, and the interpreter is real enough to run
`default-morning@1` end-to-end offline. Behavior preservation is *pinned*,
not claimed: `tests/test_workflow_engine.py` (11 cases) runs the default
chain twice on twin worlds — `run_morning` vs the engine — and asserts equal
report JSON (modulo `finished_at`), byte-equal audio, equal sidecar/manifest
(minus wall-clock fields), equal feed (minus `pubDate`, homes normalized),
and identical domain log entries in order.

Decisions:

- **Signature**: `run_workflow(session, settings, descriptor, station=None,
  *, date=None, dry_run=False)` → the plain JSON-shaped report today's CLI
  prints: `{date, outcome, stages: {...}, finished_at}`; outcomes
  `published | already_published | dry_run`. `station` is accepted but only
  threaded (identity on the context and engine events) — Station rows are
  W4 (ticket 10). `date` optional → `episode_date()`, backfill-compatible.
- **Stage keys are stage *names*** (a stage defaults to its type).
  `default-morning@1` now names its renamed stages with today's observable
  keys — `digest`/`script`/`tts` — so the report JSON, the CLI's per-stage
  prints and the characterization pins survive unchanged, and two same-type
  stages never collide. Rejected: an engine-side rename map (select→digest)
  — that hides a vocabulary migration the data can carry honestly.
  Pre-catalog, the shipped @1 file is still living data; the append-only
  rule starts with the catalog (ticket 04, W2).
- **RunContext is the one first-class channel** (Cordis ch04): session,
  settings, the validated descriptor, station, date, the artifact bus
  (named keys fixed by the stage-type triples), per-stage reports, repair
  bookkeeping (`violations`, `degrade`), pre-resolved plugins, and the
  log. No run state in globals. Runtime invariant over the channel:
  "artifact-visible means bus-keyed" — `workflow_stage_finished` records
  the `artifacts_in`/`artifacts_out` keys each stage saw (the dsh
  "model-visible means logged" pattern, adapted). Plugins keep their v1
  signatures; per-type adapters translate context → plugin; the
  context-native plugin convention arrives with registration metadata
  (ticket 05+). Rejected: threading (session, settings, artifacts…) as
  pass-through arguments (Ousterhout ch07's fix is exactly a context
  object); a global "current run".
- **Dispatch is per stage type**: collect/select run engine built-ins
  (single implementations — registries arrive when a second exists: W3
  select strategies); compose/render/publish resolve through the morning
  registries — a pinned `plugin` key wins, else the v1 unpinned rule
  (per-type settings knob `morning_tts` / `morning_publisher`, else the
  registry default); notify fans out over every registered notifier (zero
  = the designed no-op). Everything resolves *pre-flight* ("selection"
  phase, orchestrator order render→publish→compose): a bad selection
  fails loudly having done no work, stage-tagged `selection` like today.
- **Checks**: named deterministic library, fixed *binding phase* per name
  — pre-stage (`archive_intact`, validates before its stage dispatches,
  cannot repair: nothing has run yet) vs post-stage (validates the
  provided artifact). `archive_intact` consults the same publish-plugin
  guard as the run-level hook, so on the default chain the hook wins the
  race and the check cannot fire (ticket 01 recorded this); its role is
  keeping the emission guard declared and validated *in data*. v1
  implements the three the shipped descriptor carries
  (`section_allowlist`, `word_budget`, `archive_intact`); the W3 names
  (`distinct_stories`, `diversity_floor`, `duration_band`) fail loudly
  as unimplemented if encountered — never a silent pass.
- **Repair loop** (`on_fail: repair`): the failing stage re-runs with
  violation notes on the context, at most `loop_policy.max_attempts`
  times (schema-bounded 1..2, default 2), then one contained-degrade run
  (the plugin chooses its degrade; context-native plugins read
  `ctx.degrade`), then loud stage-tagged failure — at most
  `1 + max_attempts + 1` invocations, pinned by stub-plugin tests (4
  calls at max_attempts 2, 3 at 1). `on_fail` is per *check* (review
  fix): only a violating `fail` check aborts; a repairable violation
  repairs even with a fatal sibling. v1 honesty: today's plugins ignore
  the notes, so their re-runs are identical — the bound still caps a
  misbehaving plugin's blast radius; ticket 05's context-native plugins
  read `ctx.violations` / `ctx.degrade` to vary their output.
- **Log events**: the engine emits uniform
  `workflow_stage_started/finished/failed` and terminates runs with
  `workflow_run_finished/workflow_run_failed` — supersets of today's
  `morning_run_*` detail shapes (+ `workflow`, `version`, `station?`).
  The morning terminal names retire when ticket 05 routes the CLI
  through the engine: two engines must not use different names for the
  same run-level fact. Domain entries (`daily_digest_built`,
  `morning_brief_built`, `morning_audio_rendered`,
  `morning_episode_published`, `morning_notify`) are stage-owned and
  unchanged.
- **Idempotency hook**: the publish plugin's `already_published` guard,
  consulted before stage 1 → outcome `already_published`, `stages: {}`
  (today's shape, same placement; the counting-synth test proves no work
  runs). **Dry-run** skips the guard and stops before the first `publish`
  stage: publish/notify-free, but not disk-free — derived sidecar/audio
  still land under the date and the real run overwrites them.
- **Error aggregation**: any stage failure logs
  `workflow_stage_failed` + `workflow_run_failed` (`error[:300]`) and
  raises `WorkflowRunError("<stage> stage failed: …", stage=…)` — same
  message format as `MorningError`, so CLI output survives; preflight
  resolution failures are tagged `selection`. Whether `MorningError`
  aliases the engine type on the CLI surface is a ticket-05 detail.
- **Registration metadata rule fixed** (`validate_registration`): a
  plugin's declared triple may narrow, never contradict, its stage type's
  triple; the mechanism wires up at ticket 05, along with per-plugin
  param schemas (ticket 01's flag).
- **One orchestrator touch**: the compose adapter must pass pinned
  plugins through, so `stage_script` gained an optional `writer=` param
  (`run_morning` behavior unchanged).

Deferred to ticket 05 (W1 build), recorded so the prototype's seams are
explicit: flipping the `workflow/`→`morning/` import direction when the
engine routes `newsdesk morning`; the full six-check library; repair-note
ergonomics for real plugins (llm-brief's degrade is its `adapter=`
extractive path); the characterization suite adoption (ticket 03).
