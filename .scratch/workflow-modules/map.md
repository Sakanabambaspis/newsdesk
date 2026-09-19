# Wayfinder map: workflow-modules

A wayfinder map (local-markdown tracker; tickets in `issues/`, frontier =
open + unblocked + unclaimed, first by number). Work it with `/wayfinder`.

## Destination

The workflow-module architecture shipped on main: data-defined versioned
workflows run by a guardrail engine with bounded loop checks (W1), a
retrievable variant catalog (W2), rubric-scored selection strategies (W3),
multi-Station publishing with per-station feeds (W4), and MCP tools for
chat-driven customization (W5) — with W6 (elicitation playbook) scoped, ship
or defer. Done means every milestone ticket's measurable acceptance is green.

## Notes

- Founding architecture: [workflow-architecture-20260919.md](../../docs/proposals/workflow-architecture-20260919.md) — read it before any ticket.
- Consult `ousterhout-software-design` and `cordis-composability` cheatsheets each session.
- Methodology: characterization-tests-first for any behavior-preserving rewrite (see `docs/reviews/fixture-findings-20260917.md`).
- Standing decisions: **Station** (never "channel") for the output-surface entity; MCP-first chat surface on a host-neutral tool core; LLM-scored-with-stored-reasons posture (ADR 0001) — no opaque scores; deterministic validators only; seed v2 round-trips to git; **override of "plan, don't do"**: execution tickets live on this map by design — the architecture is decided, so tickets split into per-milestone decisions then measured builds.
- Conventions: ADR/posture rules in `docs/DESIGN.md`; config knobs env-wired, documented, test-pinned (config-audit 2026-09-17); every mutation actor-tagged in the log.

## Decisions so far

- [Workflow-module architecture (founding record)](../../docs/proposals/workflow-architecture-20260919.md) — 7 modules, linear stage-list interpreter over plugin registries, W1–W6 milestones, Station naming, MCP-first chat surface, rubric-as-data.
- [CI topology for multi-station runs](issues/11-ci-station-topology.md) — one scheduled workflow, matrix over station ids with `fail-fast: false` and no `continue-on-error` (isolation without killing the failure email), `morning` concurrency and 45-min per-leg timeout unchanged; per-station workflow files only if schedules diverge (`docs/research/gha-station-topology.md`).
- [Descriptor schema v1](issues/01-descriptor-schema.md) — six-stage vocabulary approved (digest→select, script→compose, tts→render); per-type Cordis triples with validator-enforced chain coherence; closed named-check set with strict per-name params and `on_fail` repair(≤2, schema-bounded)/fail; descriptor self-describes as `name`+`version`; `default-morning@1` shipped unpinned with only behavior-true checks (section_allowlist, word_budget, archive_intact) — cluster/diversity checks land with W3. Code: `newsdesk/workflow/schema.py` + `descriptors/default-morning@1.json`.
- [Engine & RunContext contract](issues/02-engine-run-context-contract.md) — `run_workflow(session, settings, descriptor, station=None, *, date, dry_run)` → today's plain report dict; **stage keys are stage names** (`default-morning@1` names renamed stages with today's keys digest/script/tts); RunContext is the one channel (bus by triple keys, repair bookkeeping, log; invariant "artifact-visible means bus-keyed" recorded on `workflow_stage_finished`); dispatch per type (collect/select built-ins; registry pins else settings knobs; notify fan-out) with pre-flight "selection" tagging; checks phase-bound per name (pre: archive_intact; post: the rest), repair ≤2 → contained degrade → loud; uniform `workflow_stage_*`/`workflow_run_*` events supersede `morning_run_*` at ticket 05; guard-before-first-stage; dry-run stops before publish. Behavior preservation pinned twin-world vs `run_morning`. Code: `newsdesk/workflow/engine.py` + `tests/test_workflow_engine.py`.

## Not yet specified

- Scoring dimensions for non-AI stations (stocks, personal email) — blocked on the rubric schema (ticket 07).
- Per-station notification routes and the F2 delivery-outbox interaction.
- Descriptor `format_version` upgrades: how station bindings migrate when the schema bumps.
- Per-station LLM cost budgets / model selection per station.
- TTS pronunciation lexicon (surfaced by the 2026-09-19 episode transcript: "Claude"→"clawed") — shape unclear until the engine contract lands.
- Per-station retention/pruning — cloud ticket 10 fixed "no prune"; multi-station may reopen it.

## Out of scope

- DAG-shaped workflows, runtime plugin hot-reload, multi-tenancy, a built-in chat UI, hosting elicitation inside newsdesk — architecture non-goals.
- 2026-09-19 incident ops cleanup (old `morning-briefing` Cloudflare project teardown; HTML-vs-JSON tolerance in the publish guard) — separate ops effort, not workflow-module design.
- GitHub Issues migration / `gh` install — tracker infra, revisit via `/setup-matt-pocock-skills`.
