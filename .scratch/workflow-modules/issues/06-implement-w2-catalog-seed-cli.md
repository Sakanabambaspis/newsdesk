# Implement W2: catalog + seed v2 + CLI

Type: task
Status: resolved
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

## Answer

Resolved 2026-09-20 (AFK build). Everything ticket 04 fixed is built and
pinned; acceptance is green (full suite 361 passed, offline).

- **Code.** `newsdesk/workflow/catalog.py`: `WorkflowCatalog(session)` —
  `create_version(doc, actor, *, via)`, `get(name, version)`,
  `resolve(ref)` (the only run-resolution path), `document(ref)`
  (inspection: retirement does not refuse — history stays readable),
  `versions(name)`, `list()`, `retire`/`unretire(name, actor, *, via)`,
  `diff(name, va, vb)`; module-level pure `diff_descriptors(a, b)` and
  `parse_ref(ref)`; `ensure_default_catalog(session)` bootstraps the
  shipped `default-morning@1` (actor=system, via=shipped) once and never
  touches the row again — the DB is the living data, the package file is
  frozen. Tables `workflows` + `workflow_versions` live in
  `core/models.py` (so `Database.create_all` picks them up; contract pin
  raised 7 → 9 tables in `test_contract_characterization.py`), their repo
  stays beside the domain package per ticket 04 (noted in DESIGN §13).
  `ACTORS` and `iso_utc` became public in `core/models.py`.
- **CLI.** `newsdesk workflow create/get/list/history/diff/retire/
  unretire` — all against a fixture DB in `tests/test_workflow_catalog.py`
  (lifecycle, bad-document and unknown-ref exits, actor-tagged log,
  `list` boots the shipped default, `get` on a hand-made versionless name
  row fails with the CLI error, never a crash). CLI mutations are
  actor=user **via=cli**; CLI seed import actor=user via=seed.
- **Seed v2.** `format: 2` gate (v1 refused); `workflows` section carries
  full descriptor documents imported in (name, version) order so any
  well-formed file satisfies the dense max+1 rule; idempotent on
  (name, version) — parsed-equal is a no-op, different is a loud
  `SeedError` ("history was violated"); export writes full history
  (name+version order); import requires `actor` (no default). The
  committed `seed/newsdesk-seed.json` was **mechanically** bumped to
  format 2 (empty `workflows`/`retired_workflows`) because CI imports it
  before the morning — the real production re-export stays ticket 12.
- **Morning seam.** `newsdesk morning` = `ensure_default_catalog` →
  `resolve("default-morning")` (floats at run start) → `run_workflow`;
  `CatalogError` renders as the CLI error exit (pinned: retired default
  refuses the run loudly). Ticket 04's "ticket 05 wires morning through
  it" lands here because `resolve()` only exists now.
- **Deliberate interpretations (record for tickets 10/12/13):**
  1. Seed retirement rides a top-level `retired_workflows: [name, …]`
     section, *not* a `retired: true` key on each entry — descriptors
     reject unknown top-level keys, so an in-document flag would fail
     `require_valid`, and retirement is name-level, not per-version.
     Monotonic: the file retires; its silence (or a stale file) never
     un-retires (pinned).
  2. The diff's `order` entry fires on **any** stage-sequence change
     (insert/remove included), not only pure reorders — the interpreter
     is linear, so position is behavior; one `/stages` entry per diff.
  3. Seed round-trips versions + documents, not authorship: imported
     versions get the importer as `created_by` (files carry documents,
     not provenance; `via: seed` in the log tells the origin story).
  4. `ensure_default_catalog` runs on *every* catalog use (all seven
     verbs, morning, seed import): "the catalog always contains the
     shipped default" — idempotent, one SELECT when already present.
  5. `document(ref)` extends the ticket-04 surface additively: inspection
     must outlive retirement, and W5's agent tools will need the same
     operation; `resolve()` remains the only run-resolution path.
- **Validation on load, everywhere.** `get`, `resolve`, `versions`,
  `diff` all re-validate stored documents + identity-match against the
  row (boundary checks; pinned by hand-corrupted-row tests). Returned
  documents are private copies — callers never alias the row's JSON.
