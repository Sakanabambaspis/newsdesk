# Implement W3: select stage + strategies + checks/repair

Type: task
Status: open
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
