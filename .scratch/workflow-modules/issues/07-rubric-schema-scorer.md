# Rubric schema + scorer contract

Type: prototype
Status: resolved
Blocked by: 01

## Question

What is a Rubric (pure data) and the scorer contract? Decide:

- rubric fields: dimensions, weights, thresholds, anchor examples — rich enough that conversational preference vocabulary ("I like deep technical substance, hate funding hype") compiles into it;
- scorer I/O: per-item score + a stored, inspectable reason (ADR 0001 posture — the model never places an item without one); where scores/reasons are stored;
- the mechanical no-key fallback formula (recency + watchlist relevance + corroboration);
- how a rubric attaches to a descriptor (inline param vs referenced catalog artifact) and whether rubrics are themselves catalog-versioned.

Deliverable: draft rubric JSON + scorer interface + a sample rubric scored
against a fixture item set, shown alongside the extractive fallback.

## Answer

Resolved 2026-09-20 (AFK — decisions made, prototyped and pinned; tickets
08/09 build on them). Grounding: founding proposal §"The seven modules"
(5), ADR 0001 (the stored-reason posture, strengthened here), Cordis ch04
(one channel: scores ride the run's artifacts and log) and ch06 (dispatch
by data: the proxy field is data, not code), Ousterhout ch4/8 (one deep
scorer interface; no knob without a consumer). Code:
`newsdesk/workflow/rubric.py` + `newsdesk/workflow/rubrics/default@1.json`
+ `BaseLLMAdapter.score_rubric` (`llm/base.py`) +
`tests/test_workflow_rubric.py` (28 pins, including the two-paths demo).
One `llm/base.py` ripple recorded: the per-method JSON-parse / item-block
duplicates are extracted into shared `_parse_model_json` / `_item_block` /
`_unparseable` helpers, so a model replying with a JSON *array* now reports
`unparseable_model_output` on the verdict path too (it previously returned
silently-empty verdicts) — the honest-cause posture, no pinned behavior
changes.

- **Rubric fields.** `format_version: 1` gate (unknown versions refused,
  descriptor precedent); identity `name@version` with the workflow name
  grammar; optional `title`. `dimensions[]`, each with `name`
  (`[a-z][a-z0-9_]*`, unique), `description` (required non-empty — the
  dimension's inspectable definition), `weight` (number > 0 — a stated
  preference with zero weight is deleted, not zeroed), optional `proxy`
  (closed set, below), optional `anchors` (non-empty list of
  `{score 0..1, example}`, no duplicate levels — the bands the scorer
  prompt quotes verbatim). `thresholds: {min_score 0..1}` — the admission
  threshold is *selection policy*, carried as data but consumed by the
  strategies (tickets 08/09) and the score-preview tool; the scorer never
  applies it. Chat vocabulary compiles into dimensions/weights/anchors:
  "I like deep technical substance, hate funding hype" is literally the
  shipped default's `technical_depth` anchors.
- **Scorer I/O.** `score_items(rubric, items, adapter, *,
  window_hours=24, now=None)` → `{rubric: "name@version", method:
  "llm:<name>" | "mechanical" | "skipped:no_items", scores: [{id, total,
  dimensions: {name: {score, reason}}}], unassessed?, llm_error?}`. The
  `total` is always computed newsdesk-side — weighted mean over the
  *scored* dimensions, so it reads 0..1 whatever the weights sum to; the
  model never emits totals (DESIGN §1.2: no opaque scores). The model
  returns per-dimension scores + reasons through
  `adapter.score_rubric` (guarded in `llm/base.py`, `classify_verdicts`
  idiom: only provided ids, only unproxied dimension names, score in
  0..1, non-empty reason). ADR 0001 strengthened: the reason is
  per *dimension*, not just per item, so the audit trail says why each
  component scored as it did. Mechanical reasons cite their numbers
  ("watchlist relevance 0.50", "published 1.0h ago (window 24h)",
  "carried by 3 distinct publishers").
- **Proxied dimensions are computed, never asked.** A dimension may bind
  one mechanical signal from the closed set `relevance` (the recorded
  weighted watchlist-term match), `recency` (`1 − age/window`, clamped
  0..1), `corroboration` (distinct publishers sharing the story key,
  capped: min(n, 3)/3). Proxies run in *every* path; the LLM scores only
  the unproxied dimensions; an all-proxied rubric needs no model at all.
- **Where scores/reasons live: run outputs, never catalog state.** They
  ride the select stage's artifacts, stage report and log (the verdict
  precedent); the rubric is versioned data, scores are recomputable per
  run — reproducibility lives in the log (ticket-04 posture). No score
  table.
- **The no-key fallback is the same formula minus the model.** Triggers:
  adapter unconfigured, `LLMError` (its failure contract — any other
  exception is a bug and stays loud), unparseable output, or an
  *incomplete* pass. The fallback is all-or-nothing — model scores and
  mechanical fills are never mixed, they are not comparable — and
  unproxied dimensions are then *excluded* (weights renormalize over what
  was scored; an unassessed dimension is not a zero) and listed in
  `unassessed`, with the cause visible in `llm_error` (the digest's
  verdict-method posture). A malformed per-item value (unparseable
  timestamp, non-numeric relevance) scores 0.0 with a visible reason — a
  degenerate input degrades the item, it never crashes the run. With the
  shipped default the formula is
  exactly the ticket's list — watchlist relevance + recency +
  corroboration: 0.5·rel + 0.25·rec + 0.25·cor after renormalizing
  (0.3/0.15/0.15 over the 0.6 proxied weight).
- **Attachment: by reference; rubrics are catalog-versioned.** A
  workflow's select stage names a rubric by ref string — `"default"`
  floats at the computed latest, `"default@N"` pins, resolved at run
  start (ticket-04 float/pin semantics applied; storage mechanics are
  ticket 09's build). Rubrics are catalog-versioned with ticket-04
  semantics wholesale: INSERT-only versions, dense and never reused,
  required actor, computed latest, name-level retire, structural diff.
  Never inline: rubrics are shared across workflows, chat-edited
  independently (a taste tweak must not mint a workflow version), and
  diffable on their own. A missing or retired ref fails the run loudly at
  pre-flight (`selection`) — never a silent fallback.
- **`story_key` unified.** Corroboration shares the syndication check's
  story identity, so the normalized-headline key moved from the engine's
  private `_story_key` to public `story_key` in `rubric.py` — one
  definition, the engine delegates; ticket 08 still owns the formal spec.
  Verbatim move; characterization suite green.
- **Demo (pinned, `test_rubric_preferences_reorder_the_pack`).** Same
  seven-item fixture set (deep vs hype vs a three-outlet syndication
  triplet vs an old exclusive vs an off-topic item): the mechanical
  formula ranks the funding story 4th of 7 — above the deep-dive —
  because it cannot see substance; scoring the same set against
  `default@1` (stubbed `technical_depth` pass) reorders deep 2nd and hype
  6th. Preference compilation changes the outcome exactly where taste
  should, with a stored reason for every dimension of every placed item.
