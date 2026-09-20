# Implement W3: select stage + strategies + checks/repair

Type: task
Status: resolved
Blocked by: 05, 08

## Question

(HITL build) Implement the `select` stage per ticket-08 decisions: the three
strategy plugins, cluster collapse, diversity floor, rubric scoring with the
mechanical fallback, and the bounded repair loop wired through the engine.

Acceptance (measurable):

- all three strategies runnable via descriptor param and selectable in a dry-run;
- the repair loop is bounded and unit-tested (never exceeds policy attempts; degrade path contained);
- the 2026-09-19 failure shape is covered by a regression test: three syndicated copies of one story can no longer fill three episode slots;
- the run report includes pack stats (candidates / after-filter / in-pack) so selection shrinkage is visible per run.

## Answer

Resolved 2026-09-20 (AFK build). Ticket 08's build spec is implemented and
pinned; acceptance is green (full suite 426 passed, 3 pre-existing
environment skips, fully offline).

- **Strategies.** `newsdesk/workflow/strategies.py`: `SELECT_STRATEGIES`
  (a `Registry` beside the morning ones, no settings knob) with the three
  plugins registered `stage="select"` and closed param key sets —
  `top-k-interesting {rubric, k, hours?}`, `trending-impactful-mix
  {rubric, k, hours?}`, `single-deep-dive {rubric, hours?}`. One common
  pipeline (`_select`): window → 30-cap (the cap is absolute —
  `candidate_items` clamps to `min(limit, 30)`, a clamp the legacy code
  had and was nearly lost in the extraction) → verdict filter
  (`classify_verdicts` + `technical_only`, extracted verbatim from the
  legacy digest) → `score_items` over all post-filter candidates →
  `min_score` admission → pick → `build_material_pack` in final episode
  order under the existing budget caps (writer contract untouched).
  Cluster collapse groups the admissible set by `story_key`;
  representative = best total, fresher, id; cluster `outlets` = distinct
  publishers across the *scored* set via the rubric module's
  `publisher_counts` — literally the number the corroboration reason
  cites, even when `min_score` evicts copies (pinned by test). Mix
  construction: best-impactful first, `floor(k/2)` trending slots
  (outlets desc, then total), impact fills to k — both pools rank one
  story set, so the two takes *are* the mutual backfill (packs shrink,
  never pad; a first draft's third "backfill" take was provably dead and
  removed). Scores, reasons, rubric ref and method ride the select
  artifact (`scoring`), the select report (including the full per-item
  `scores`) and the `select_scored` log entry.
- **Rubric catalog (the ticket-07 storage mechanics).**
  `newsdesk/workflow/rubric_catalog.py`: `RubricCatalog` — the workflow
  catalog's twin with ticket-04 semantics (dense never-reused versions,
  computed latest, required actor on every mutation, retire/unretire,
  validation on save AND on load, private document copies, loud
  `CatalogError`s); `rubrics` + `rubric_versions` tables in
  `core/models.py` (contract pin 9 → 11);
  `ensure_default_rubric_catalog` bootstraps the shipped `default@1`
  (actor=system, via=shipped) once. Deliberately absent: a structural
  diff and seed sections (no consumer yet — CRUD surfaces are the
  agent-tool tickets), and a shared VersionedCatalog base (rejection
  recorded in the module docstring). `catalog.parse_ref` gained a `kind`
  noun for error messages.
- **Engine wiring.** Pinned select resolves through `SELECT_STRATEGIES`
  at pre-flight (`selection` tag on unknown strategy); the `rubric` param
  is required and resolved through the rubric catalog at pre-flight
  (float `default` / pin `default@1`, loud on missing/retired/malformed
  — zero work done); the shipped rubric bootstraps at first *use* (a
  pinned select exists), so legacy runs touch nothing. `RunContext`
  carries the resolved rubrics keyed by stage name. The select report
  carries method/rubric/scoring_method/min_score/window/verdict_method +
  candidates/after_filter/in_pack + scores. Two select stages pinning
  *different* strategies are refused loudly (one strategy per stage
  type, same as the other registries).
- **The floors measure the admissible set.** The select artifact exposes
  `admissible` (post-verdict-filter, post-min_score candidate cards);
  both coverage checks' offered-base moved from pack items to it — a
  small pick can no longer lower its own floor (pinned: single-deep-dive
  under the default 2-theme floor now fails loudly where the pack-based
  base passed). The checks stay bound post-compose; strategies never
  enter the repair loop — and that claim is now *enforced*: a check
  bound where its artifacts cannot exist (e.g. coverage checks at
  select) is refused at pre-flight instead of burning bounded attempts
  on a stage that cannot self-heal (`_CHECK_REQUIRES`). The bounded loop
  on strategy workflows is pinned (4 compose invocations = 1 + 2 repairs
  + contained degrade, then loud, nothing published).
- **Deliberate interpretations / sanctioned pin flips:**
  1. Pack stats in the *run report* are the strategy select report's;
     the legacy unpinned select report stays verbatim
     (frozen-`default-morning@1` rule) — the legacy path's shrinkage
     numbers live in `daily_digest_built`'s material pack as before.
  2. Characterization digest-shape pin gains `admissible` (equal to
     `items` on the keyless path); table pin 9→11; coverage-check
     messages say "the admissible set offers N" — the ticket-08-
     sanctioned flips.
  3. Empty-key items are excluded from story counts and never *picked*
     (strategies pick stories); they remain in the material via the
     legacy path — "still packable, still speakable" is about the
     filter, not the strategies.
  4. Story-level tiebreaks ("then id") use the representative's id —
     stories inherit their representative's total order.
  5. `k`/`hours` value validation lives in the strategy (select-stage
     failure); only the rubric ref is pre-flight, per ticket 08's
     letter.
  6. No rubric CLI verbs or seed sections in this ticket — storage +
     resolution only; surfaces land with the agent tools (13/14).
- **Code:** `workflow/strategies.py`, `workflow/rubric_catalog.py`,
  `workflow/rubric.py` (public `publisher_counts`),
  `workflow/catalog.py` (`parse_ref` kind), `workflow/engine.py`,
  `pipeline/digest.py` (`candidate_items`/`classify_verdicts`/
  `technical_only`/`item_card`), `pipeline/material.py` (conditional
  `outlets` on pack cards), `core/models.py`. Tests:
  `tests/test_workflow_select_strategies.py`,
  `tests/test_workflow_rubric_catalog.py`; re-pins in
  `test_workflow_coverage_checks.py`,
  `test_characterization_default_chain.py`,
  `test_contract_characterization.py`, plus the cap pin in
  `test_digest.py`.
