# Implement W1: schema + engine (behavior-preserving)

Type: task
Status: resolved
Blocked by: 01, 02, 03

## Question

(HITL build) Implement `newsdesk/workflow/` per the ticket-01 schema and
ticket-02 contract; encode `default-morning@1`; attach the 2026-09-19 checks
(syndication-cluster collapse, cross-theme diversity floor) to the default
descriptor; route `newsdesk morning` through the engine.

Acceptance (measurable):

- the characterization suite from ticket 03 passes unchanged;
- the engine runs `default-morning@1` end-to-end offline (stubbed LLM/TTS, `local-dir` publisher);
- the two repro loops from the 2026-09-19 diagnosis now pass their assertions (episode coverage is check-guarded, not structurally capped at pack[0:4]);
- the full existing test suite is green.

## Answer

Resolved 2026-09-20. W1 shipped: `newsdesk morning` routes through the
engine, the six-check library is complete, and the 2026-09-19 repro loops
are check-guarded. Acceptance proven: characterization suite untouched and
green (6 passed); engine runs `default-morning@1` end-to-end offline; the
full suite is green (313 passed, +12 new tests in
`tests/test_workflow_coverage_checks.py`).

Decisions:

- **The CLI is the routing point; `run_morning` stays as the
  characterization specimen.** `newsdesk morning` loads the shipped
  `default-morning@1` (catalog-blind until W2 — `load_descriptor`, replaced
  by the catalog's `resolve("default-morning@latest")` at ticket 06) and
  calls `run_workflow`, catching `WorkflowRunError` (same message format as
  `MorningError`, so CLI output and exit codes survive). Deleting
  `run_morning` would have broken ticket 03's untouched suite, so the
  legacy chain remains as the pinned specimen — its docstring records the
  retirement path (W2's shipped-descriptor bootstrap). Consequently the
  ticket-02 "flip the `workflow/`→`morning/` import direction" is
  superseded for W1: `engine → morning.orchestrator` stays (the engine
  composes the plugin implementations), and the one new opposite edge is
  `morning.registries → workflow.schema` for registration-time triple
  validation — one-directional per module, no cycle; `workflow/__init__`
  must stay import-light (constraint recorded there). The full flip
  happens when the specimen retires.
- **The 2026-09-19 checks attach to compose (post), not select.** The
  acceptance is "episode coverage is check-guarded": `distinct_stories` and
  `diversity_floor` guard what the *episode* covers against what the *pack*
  offered — the diagnosed failure (a syndication cluster filling pack[0:4]
  while distinct stories sat ranked below) is invisible to a pack-level
  check. Provisional spec, formalized at ticket 08: story identity =
  normalized headline (wire reprints share the wire headline); theme =
  matched watchlist terms; **the floor scales to the material**
  (`min(min_distinct, offered)` / `min(min_themes, offered)`) so a quiet
  day or single-theme pack legally passes — the absolute floors ticket 01
  rejected are expressible but never shipped. On violation: bounded
  repairs (compose re-runs are deterministic, so they cannot self-heal —
  the notes are there for W3's strategies), contained degrade, loud
  stage-tagged failure, nothing published. `duration_band` is implemented
  (post-render) but unattached in @1 — a real band is a policy number for
  a v2. `pack[0:4]` itself is untouched; W3 replaces selection.
- **Registration metadata wired** (ticket 01/02's mechanism move):
  `Registry.register(key, fn, stage=..., requires=..., provides=...,
  params=..., context=...)` — the triple is validated at registration
  (narrow-never-contradict; contradiction is a loud `ValueError` at
  definition), `params` closes the plugin's stage-params key set, and
  `context=True` marks a context-native plugin. The built-ins declare
  their stages; `llm-brief` is context-native.
- **Per-plugin param schemas landed as closed key sets.** Each plugin
  declares the param keys it accepts; the engine validates every stage's
  params at pre-flight (unknown key → `selection`-tagged failure, zero
  work). The select built-in declares `{"hours"}`; the three registry
  built-ins declare none (they read settings, not params). Fail-closed: an
  undeclared param is rejected, so new plugins must declare. Structurally
  opaque values stay open in v1 (the descriptor validator never sees
  plugin params — the engine, which resolves plugins, is the witness).
- **Context-native degrade is contained.** `stage_script(..., ctx=...)`
  forwards the RunContext only to plugins registered `context=True`;
  `llm-brief` skips its LLM call on the contained-degrade pass
  (`ctx.current_stage in ctx.degrade`) — the degrade is extractive
  directly, pinned by an adapter-call-count test (3 calls: initial + 2
  repairs; the 4th skips the model). `ctx.violations` is delivered but
  unused by llm-brief: its contract fixes the assignment (pack[0:4]),
  only prose varies — acting on notes is W3's re-selection.
- **`validate_registration` moved to `workflow/schema.py`** (the engine
  re-exports it, so ticket 02's pinned imports hold): the registries
  validate at registration and cannot import the engine. Ticket 02's
  answer says "shipped in engine.py" — the rule is unchanged, only the
  home moved with the wiring.
- **Registry slots are uniform** `(resolved_name, plugin)` pairs (render
  joined publish/compose), resolved via the new `Registry.resolve()`;
  `RunContext.current_stage` lets context-native plugins locate their own
  bookkeeping.

Rejected: attaching the coverage checks at select (misses the diagnosed
failure — the pack legitimately spans many stories while the slots
collapse); failing quiet days loudly (ticket 01's recorded posture); an
engine-side rename map; making `MorningError` alias `WorkflowRunError`
(two engines, two exception types — the CLI now catches only the engine's).
