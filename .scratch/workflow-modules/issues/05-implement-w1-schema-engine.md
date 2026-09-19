# Implement W1: schema + engine (behavior-preserving)

Type: task
Status: open
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
