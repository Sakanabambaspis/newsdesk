# Engine & RunContext contract

Type: prototype
Status: open
Blocked by: 01

## Question

What is the engine's exact contract? Decide:

- `run_workflow(session, settings, descriptor, station, date) -> RunReport` — signature, report shape, dry-run mode.
- RunContext contents: the artifact bus (named keys, who provides/requires), per-stage reports, log threading — one first-class channel, no globals (Cordis context paradigm).
- Stage dispatch via the registries; between-stage check enforcement; bounded repair-loop interface (fail → targeted stage re-run with failure notes, ≤2 attempts → contained degrade → loud stage-tagged failure).
- Per-stage log events (`workflow_stage_started/finished/failed`); idempotency hook point; error aggregation (MorningError-style, stage-tagged).

Deliverable: a written contract + skeletal signatures, shown
behavior-preserving against today's `newsdesk/morning/orchestrator.py` — the
observable behavior of the default chain must be reproducible through the
engine (pin via the characterization suite from ticket 03).
