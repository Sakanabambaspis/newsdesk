# Implement W2: catalog + seed v2 + CLI

Type: task
Status: open
Blocked by: 04, 05

## Question

(HITL build) Persist the catalog per ticket-04 semantics: `workflows` +
append-only `workflow_versions` tables (`create_all`, no alembic); CLI verbs
(`newsdesk workflow create/get/list/history/diff/retire`); seed format 2 with
a `workflows` section; the default descriptor seeded into the DB.

Acceptance (measurable):

- seed export → import round-trip preserves workflows (idempotent, never deletes);
- create/get/list/history/diff/retire all work via CLI against a fixture DB;
- every mutation lands in the log, actor-tagged;
- new env-wired knobs (if any) are documented and test-pinned per the config-audit conventions.
