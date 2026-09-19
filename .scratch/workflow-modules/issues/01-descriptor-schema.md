# Descriptor schema v1

Type: prototype
Status: resolved
Blocked by:

## Question

What exactly is the workflow-descriptor JSON, version 1? Decide:

- Stage vocabulary: `collect`, `select`, `compose`, `render`, `publish`, `notify` — is this the right closed set, and what does each require/provide (Cordis triple)?
- StageSpec fields: plugin registry key (or default), params, per-stage `checks`.
- Check-spec shape: named deterministic checks with params (e.g. `distinct_stories`, `diversity_floor`, `word_budget`, `duration_band`, `section_allowlist`).
- Workflow-level guardrails and params; `format_version` gate (precedent: seed `format: 1`).

Deliverable: a draft schema (JSON example + validator sketch) and the current
morning chain expressed as a `default-morning@1` descriptor that validates
against it. Founding context:
[workflow-architecture-20260919.md](../../docs/proposals/workflow-architecture-20260919.md)
§"The seven modules" (1, 2) and §"Deterministic guardrails".

## Answer

Resolved 2026-09-20. Shipped as `newsdesk/workflow/schema.py` (+ validator
tests `tests/test_workflow_schema.py`, 23 cases) and the shipped descriptor
`newsdesk/workflow/descriptors/default-morning@1.json`. Validation is
deterministic, hand-written (no jsonschema dep), returns *all* violations at
once (`validate_descriptor` → list of strings; `require_valid` raises
`DescriptorError` with the full list) — the chat surface will want complete
error reports, not one-per-round-trip.

Decisions:

- **Stage vocabulary approved as proposed**: `collect, select, compose,
  render, publish, notify`. Today's chain maps with renames only — digest→
  `select` (ADR 0001: digest-as-selector), script→`compose`, tts→`render`.
- **Cordis triple per stage *type*** (v1 constant, not per-plugin): collect
  ∅→`collection`; select `collection`→`digest`; compose `digest`→`script`;
  render `script`→`audio`; publish `audio`→`episode`; notify `episode`→∅
  (effects are fixed by type; publish is the emission stage). The validator
  *enforces* chain coherence: every requires must be provided by an earlier
  stage. Per-plugin requires/provides metadata arrives at registration
  (ticket 02) and will be checked against this table.
- **StageSpec**: `{type, name?, plugin?, params?, checks?}`. `name` defaults
  to the type (must be unique — repeated types need distinct names).
  `plugin: null`/absent = unpinned (engine resolves; today the env-wired
  settings knobs decide — that resolution rule is ticket 02's). `params` is
  structurally opaque in v1 (object); closing per-plugin param schemas is
  registration metadata (ticket 02), flagged there.
- **Check specs**: closed set `{distinct_stories, diversity_floor,
  word_budget, duration_band, section_allowlist, archive_intact}`, each
  `{name, params?, on_fail?}`; params validated per name (strictly — unknown
  keys rejected everywhere; witnesses at definition). `on_fail`: `repair`
  (targeted re-run ≤ `loop_policy.max_attempts`, then contained degrade,
  then loud) or `fail` (loud immediately — archive_intact). The ≤2 bound is
  schema-enforced (`max_attempts` must be 1..2); unbounded loops are not
  expressible.
- **Workflow level**: `{format_version, name, version, title?, params?,
  loop_policy?, stages}`. `format_version` gate = 1, refuse others (seed
  `format: 1` precedent). `name`/`version` live *in* the document (self-
  describing, git round-trip); the catalog row must match (ticket 04).
- **`default-morning@1` encodes today's chain honestly** — unpinned plugins,
  select `{"hours": 24}`, and only the checks that are true today without
  changing behavior: compose gets `section_allowlist` (the fixed
  SECTION_TYPES) and `word_budget` (headline 60 / deep_dive 460 — the caps
  `clip_words` mechanically guarantees, so the check cannot fire on
  today's output); publish gets `archive_intact` (the existing refusal,
  expressed as data). NOT attached yet: `distinct_stories` +
  `diversity_floor` (the 2026-09-19 fixes) — attaching them changes failure
  behavior, so they land with W3/ticket 05 as the proposal schedules.
- **Deliberate limitations recorded**: word *floors* (DEEP_DIVE_MIN_WORDS,
  400–450 target) stay compose-internal quality gates, not guardrail
  checks — a quiet day legally yields a short episode, so a floor would
  break behavior preservation; the "contained degrade" a repair may fall
  back to is chosen by the plugin (llm-brief → extractive), not named in
  data; `workflow/` importing `SECTION_TYPES` from `morning/script.py` is
  the honest dependency for now and flips when the engine routes morning
  (ticket 05).
