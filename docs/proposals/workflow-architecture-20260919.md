# Workflow modules, rubrics, and stations — architecture proposal

Date: 2026-09-19. Status: approved high-level direction. Mid/low-level design is
worked on the wayfinder map at `.scratch/workflow-modules/` (see that map's
frontier before implementing anything here).

## Goal

Turn the hardcoded morning chain (collect → digest → script → tts → publish →
notify) into **data-defined, versioned workflows** that:

- are standardized, with deterministic guardrails and bounded loop checks;
- are editable by an agent through a chat surface (tools + validation, never
  raw code edits);
- keep every variant in a retrievable catalog (name@version, diff, retire);
- bind to multiple **Stations** — one podcast = its sources + workflow + its
  own published feed ("Station" is the deliberate glossary term; "channel"
  already means a source in DESIGN.md).

## Framework grounding

**Ousterhout (deep modules, information hiding).** Exactly two deep
interfaces: the **workflow descriptor** (a JSON document hiding "what a
workflow is") and the **engine** (`run_workflow(descriptor, station, date) ->
RunReport`, hiding dispatch, guardrail enforcement, repair loops, logging,
error aggregation). Everything else is a plugin behind a narrow call, matching
the existing `Registry` idiom. Modules are structured around knowledge (schema,
validation, catalog, stations), not execution order — stages are data, not
modules.

**Cordis (composability).** Every stage plugin declares a component triple —
required artifact keys, provided artifact keys, effects (publish is an
*emission*; its "inverse" is the existing archive-intact refusal, never
deletion). All state flows through one first-class **RunContext**; every
artifact a stage saw is logged. Confluence: a run is a pure function of
(descriptor, plugin registry, inputs), so temporal composability comes from
the versioned catalog + reproducible re-runs — runtime hot-reloading is out of
scope for a batch pipeline. Registration tables, not ordered chains;
retire-never-delete.

**Design-it-twice (rejected alternatives).** (a) Workflows-as-Python-plugins
in a registry — not data, not agent-editable, not catalogable. (b) A reactive
artifact-bus dataflow — interface explosion for a linear batch domain;
requires/provides metadata keeps the upgrade path open. Chosen: a declarative
**linear stage list + interpreter**.

## The seven modules

1. **Descriptor schema** — the workflow definition language: ordered stages
   (stage type + plugin ref + params + per-stage checks), workflow-level
   guardrails and params. Pure data + a validator enforced on save and load.
   Seed-format v2 gains `workflows`/`stations` sections with the existing
   `source_keys`-style identity indirection.
2. **Stage contracts & registries** — six stage types: `collect`, `select`,
   `compose`, `render`, `publish`, `notify`. The four existing registries fold
   under these; plugins declare requires/provides/effects metadata at
   registration. Artifacts stay plain dicts with named keys.
3. **Engine** — the interpreter and the biggest complexity sink: dispatch,
   RunContext threading, `workflow_stage_started/finished/failed` log entries,
   between-stage checks, bounded repair loops, idempotency per
   (station, date), stage-tagged error aggregation. `newsdesk morning` becomes
   a thin wrapper: load `default-morning@latest` → engine.
4. **Catalog** — `workflows` + append-only `workflow_versions` tables; create/
   update (new version, never mutate history), get by `name@version` or
   latest, list, diff, retire (flag). Actor-tagged audit (`actor=agent` for
   chat edits). `create_all` — no alembic.
5. **Rubric & selection strategies** — the `select` stage replaces today's
   fixed material-pack logic. Strategy plugins (`top-k-interesting`,
   `trending-impactful-mix`, `single-deep-dive`) deterministically select from
   items scored against a **Rubric**: a stored data artifact (dimensions,
   weights, thresholds, anchors) that conversational preference vocabulary
   gets compiled *into*. Scoring is LLM-graded per rubric dimension with a
   stored reason per item (ADR 0001 posture); a mechanical keyword formula is
   the no-key fallback. Lands the 2026-09-19 fixes as default behavior:
   syndication-cluster collapse and a cross-theme diversity floor.
6. **Stations** — new glossary term in CONTEXT.md (which explicitly demands a
   new term for per-watchlist briefings). Station = scope (sources/watchlists)
   + workflow ref (`name@version` or latest) + feed identity (title/author/
   category/artwork) + publish target (per-station token path under the
   existing pages project). Fixes: single feed token, constant feed identity,
   date-only GUIDs, global include-terms scoping (this is F1's per-watchlist
   digest scoping).
7. **Agent tools** — host-neutral tool functions surfaced as MCP tools first
   (the harness is the conversation; auth and dialog inherited; HARNESS.md's
   thin-adapter posture). Tools: workflow/rubric/station CRUD, catalog search
   + diff, **dry-run** (engine with publish/notify skipped), score-preview. A
   built-in chat API remains a deferred option on the same tool layer.

**Preference elicitation** (agent clarifying fuzzy taste into a rubric) is
deliberately *not* a newsdesk module: it lives in the harness agent as a dialog
playbook over the tools above. Newsdesk's obligation is that rubrics are cheap
to create, validate, simulate, and diff.

## Deterministic guardrails & loop checks

Between-stage checks: descriptor schema validity; distinct-stories
(cluster collapse) in the pack; cross-theme diversity floor; word/section
budgets against the sidecar schema; section-type allowlist; episode duration
bands; archive-intact pre-publish. Loop policy per check: `fail → targeted
stage re-run with failure notes (≤2 attempts) → contained degrade (e.g.
extractive prose) → loud stage-tagged run report`. No LLM in any validator.

## Milestones

- **W1 — schema + engine, behavior-preserving.** Default descriptor encodes
  today's chain; characterization tests pin current outputs.
- **W2 — catalog + seed v2 + CLI.**
- **W3 — rubric + select strategies + check/repair library.**
- **W4 — stations.** Feed parametrization, per-station publish targets/GUIDs/
  idempotency, per-station digest scoping, CI fan-out.
- **W5 — MCP tools + dry-run.** Chat-driven customization end-to-end.
- **W6 (experimental) — elicitation playbook + score-preview refinement.**

Order rationale: interface first, then persistence, then policy variants, then
surfaces, then conversation. Each step keeps previous shipped behavior intact.

## Non-goals (this round)

DAG-shaped workflows; runtime plugin hot-reload; multi-tenancy (DESIGN §1.2);
a built-in chat UI; hosting the elicitation conversation inside newsdesk.

## Relationship to existing commitments

- **ADR 0001** (digest-as-selector): unchanged in spirit — the select stage
  generalizes it; the model never places an item without a stored, inspectable
  reason; the rubric *is* the inspectable standard.
- **CONTEXT.md glossary**: Station enters as a new term; Episode's
  one-episode-per-digest line gets its promised successor wording.
- **Proposals 2026-09-16**: F1 (per-watchlist digest runs) lands inside
  stations; F2 (outbox) and F3 (render dispatch) stay future fog; F6 trigger
  tagging applies to station runs.
- **DESIGN §10 / HARNESS.md**: tools stay thin; state lives in newsdesk; every
  mutation actor-tagged; credentials env-only.
