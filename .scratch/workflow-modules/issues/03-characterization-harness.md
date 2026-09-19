# Characterization harness for the default chain

Type: task
Status: open
Blocked by:

## Question

(AFK, no decision) Pin the current pipeline's observable behavior offline, so
the W1 engine rewrite is provably behavior-preserving — the repo's
characterization-first methodology (`docs/reviews/fixture-findings-20260917.md`).

Work: characterization tests over the existing chain (`orchestrator.run_morning`
and its stage functions) using fixture feeds/DB per `tests/` conventions:

- digest shape for a fixture window (`build_daily_digest` keys, sections, counts);
- script sidecar sections/stats for a stubbed LLM adapter (extractive and llm paths);
- audio report shape for a stubbed synth; publish report + manifest for `local-dir`;
- full-run report JSON and the log-entry sequence for the default chain.

Acceptance (measurable): the suite runs with no network and no LLM key; it
pins the default chain's observable outputs; it is red-capable if any stage's
behavior changes. Findings land in `tests/` as normal pytest files.
