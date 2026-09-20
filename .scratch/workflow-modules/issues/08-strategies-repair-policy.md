# Strategies + repair policy

Type: grilling
Status: resolved
Blocked by: 02, 07

## Question

Decide the selection semantics and the loop policy:

- the three launch strategies in terms of rubric scores + verdicts + syndication clusters: `top-k-interesting` (k param), `trending-impactful-mix` (what do "trending" and "impactful" mean measurably?), `single-deep-dive`;
- cluster collapse rule (one story per syndication cluster; cluster size spoken as "covered by N outlets");
- cross-theme diversity floor: formal check spec (what counts as a theme, what is the floor);
- repair-loop policy numbers per check: attempts (≤2?), degrade path (extractive vs fail-loud), what failure notes the re-run sees;
- whether strategy choice is a descriptor param or separate registered plugins per strategy (recommend: plugins).

Answers are the build spec for ticket 09.

## Answer

Resolved 2026-09-20 (decision ticket — no code here; this is the build spec
ticket 09 implements). Grounding: founding proposal §"Rubric & selection
strategies" and §"Deterministic guardrails & loop checks"; ADR 0001 (the
stored-reason posture); Cordis (dispatch by data — the strategy is pinned
data, not an enum switch; one channel — scores ride the run); Ousterhout
(no knob without a consumer; one deep seam: strategies own the order, the
writer owns the prose). `story_key`'s formal spec, owned here per ticket
07, is fixed below unchanged from its implementation.

- **The common pipeline (all strategies share it, in order).** window
  (`hours`, default 24) → the existing 30-item candidate cap → verdict
  filter (technical-only when verdicts exist, `skipped` fallback —
  unchanged) → rubric scoring of **all** post-filter candidates
  (`score_items`, ticket 07: one LLM pass, all-or-nothing mechanical
  fallback) → admission (`thresholds.min_score`: stories scoring below
  the bar are not packable; packs shrink, never pad — the quiet-day
  posture) → the strategy picks stories → the material pack in **final
  episode order** under the existing budget caps (30 items / 1,200 /
  24,000 chars). The writer contract is untouched — deep dive =
  `pack[0]`, headlines = `pack[1:4]`, a `k` above 4 pads the writer's
  context without new spoken slots. Scoring runs before collapse (not
  after) because the corroboration signal counts publishers across the
  candidate set — `score_items` derives it from the scored list itself,
  and duplicate wire copies inside the 30-cap are an acceptable cost.
  Scores, reasons, rubric ref and method ride the select report and log
  (ticket 07), alongside the pack stats (candidates / after-filter /
  in-pack).

- **Cluster collapse (formal).** A **story** is `story_key` — the
  normalized headline: lowercase, every non-alphanumeric run → one
  space, trimmed; items with no usable title carry the empty key and are
  excluded from story counts (still packable, still speakable). That is
  the whole v1 spec: wire reprints share the wire headline; the same
  story under two different headlines is two stories — deterministic
  beats clever, fuzzy matching stays fog. Collapse groups stories at
  pick time; each cluster fields one **representative**: highest rubric
  total, then fresher `published_at`, then lexicographic `id` — a total
  order, no heuristics. The cluster's size = distinct publishers among
  its candidate copies, carried on the pack card as `outlets` and spoken
  in prose as **"covered by N outlets"** (the corroboration reason's
  "carried by N distinct publishers" is the same number computed the
  same way).

- **The three strategies — separate registered plugins (the
  recommendation stands).** The select stage pins one via `plugin`;
  **unpinned select stays the legacy built-in digest**, which is exactly
  how frozen `default-morning@1` keeps its characterization-pinned
  behavior — strategies enter through new workflow versions. One plugin
  per strategy with its own closed params key set; the rubric ref is a
  required param resolved at pre-flight (`selection` tag; missing or
  retired fails loudly — ticket 07 semantics; storage mechanics are
  ticket 09's build). Rejected: one plugin with a `mode` param — an enum
  switch hides each strategy's policy and fakes the closed key sets
  (`k` is meaningless to the deep dive). A new select-strategy registry
  mirrors the compose/render/publish pattern; no settings knob for
  select (no consumer).
  - `top-k-interesting` — params `{rubric, k ≥ 1, hours?}`. The `k` best
    stories by rubric total (ties: fresher, then id). "Interesting" =
    the rubric total, nothing else.
  - `trending-impactful-mix` — params `{rubric, k ≥ 1, hours?}`.
    Measurable **trending** = corroboration breadth: distinct
    publishers carrying the story (the mechanical newsroom signal; no
    external metrics in v1). Measurable **impactful** = the rubric
    total. Construction: `pack[0]` = the best-impactful story (the deep
    dive is substance); then `floor(k/2)` trending slots (outlets desc,
    then total), then impactful fills the rest; dedup by story, and
    each pool backfills the other when it runs dry.
  - `single-deep-dive` — params `{rubric, hours?}`. Exactly one story:
    the top total. No admissible story → empty pack → the quiet-day
    short episode. Never padded.

- **Cross-theme diversity floor (formal spec — upgrades the
  provisional).** A **theme** is a matched watchlist term
  (`matched_terms`, non-empty); an item matching nothing contributes no
  theme. Both coverage floors scale down to what the material offered,
  never up from it — a quiet day never fails (kept). The upgrade:
  "offered" is the **admissible set** — post-verdict-filter,
  post-`min_score` candidates, i.e. what selection could legally have
  packed — not the pack the strategy emitted. Under W1 the pack *was*
  the selection so the distinction was vacuous; once a strategy outputs
  a small pack, floors measured against it would collapse to the very
  failure they guard (a single-theme pick would lower its own floor to
  1 and pass). So: `distinct_stories` = covered distinct story keys ≥
  `min(min_distinct, admissible keys)`; `diversity_floor` = spanned
  themes ≥ `min(min_themes, admissible themes)` — both measured
  episode-vs-admissible. The select artifact must therefore expose the
  admissible set (exact field shape: ticket 09), and the W1 checks'
  offered-base moves from pack items to it — for the built-in path that
  differs only by the verdict filter and budget deltas, so passing
  default-chain runs are unaffected; ticket 09 re-pins the coverage
  tests against the new base. The checks stay bound **post-compose** —
  their artifacts (pack + script) only coexist there, and in fact no
  member of the closed check set can bind at select (each needs a later
  artifact; the binding guard already refuses loudly) — so strategies
  never enter the repair loop: collapse is true by construction, and
  the coverage pair remains the defect backstop (a buggy strategy that
  packed duplicates still fails `distinct_stories` at compose).

- **Repair-loop policy (the numbers).** `loop_policy.max_attempts`
  stays **per-workflow**, schema-bounded 1..2, default 2 — a per-check
  budget is rejected (attempt budgets compound across sibling checks;
  one number, one place). Worst case = `1 + max_attempts + 1` stage
  invocations (4 at the default: initial, two repairs, one contained
  degrade) — the pinned llm-brief adapter-call count already matches.
  `on_fail` stays **per check**: `archive_intact` = `fail` (the archive
  never degrades); every other shipped check = `repair` (the default).
  The degrade path is **contained extractive, then loud** — there is no
  third way: compose's degrade is the extractive prose fill (llm-brief
  skips the model, pinned at ticket 05); after the degrade pass the
  checks run once more and a persistent violation aborts the run
  stage-tagged with **nothing published**. What the re-run sees:
  exactly the current attempt's violation strings —
  `check '<name>': <message>` — on `ctx.violations[stage]`, replaced
  per attempt; full history lives only in the log. And the thread
  ticket 05 left open closes here: **no plugin acts on the notes in
  v1.** Compose re-runs are deterministic and cannot self-heal, and
  W3's defense against the 2026-09-19 shape is intrinsic collapse at
  select — so a fired coverage check now means a defect, and the right
  behavior is contained degrade then loud failure, not an automatic
  workaround publishing a policy-violating episode. A workflow that
  prefers availability over a floor loosens the check in its own
  version — policy is data, not plugin behavior.
