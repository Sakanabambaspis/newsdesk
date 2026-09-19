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

## Comments

2026-09-20 (ticket 04): the semantics are fixed — see ticket 04's Answer for
the full contract (dense never-reused integer versions, computed `latest`,
INSERT-only versions with required actor, name-level `retired_at` flag,
structural diff, validate on save and on load). Two deltas to this ticket's
scope: the CLI verb list gains **`unretire`** (retire is reversible, delete
is not expressible), and seed import applies retirement **monotonically** —
`retired: true` retires on import, but a seed never un-retires. The shipped
`default-morning@1` bootstraps the catalog idempotently (actor=system,
via=shipped) and freezes as package data.
