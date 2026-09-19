# Catalog versioning semantics

Type: grilling
Status: resolved
Blocked by: 01

## Question

What are the workflow catalog's versioning semantics? Decide:

- `name@version` resolution: what does `latest` mean, how do station bindings pin or float;
- immutability rules: append-only `workflow_versions`; update = new version, never mutate history; who may create versions (actor tags);
- retire flag vs delete (retire-never-delete); what a retired workflow does to stations bound to it;
- diff format between two versions (human- and agent-readable);
- seed v2 sections (`workflows`, later `stations`) with `source_keys`-style identity indirection, format gate, and idempotent import;
- validation-on-save vs validation-on-load obligations (Cordis: witnesses at definition).

Answers feed tickets 06 (implementation), 10 (station bindings), 12 (seed v2).

## Answer

Resolved 2026-09-20 (AFK — decisions made and recorded here; ticket 06 builds
them). Grounding: founding proposal §"The seven modules" (4), Cordis ch05
(retire-vs-remove, withdrawal surfaces to dependents) and ch08 (the versioning
gap: constrain versions at link time; nominal keys carry no version semantics
of their own), Ousterhout ch4/8 (no extra state or knobs without a consumer).

- **Identity and resolution.** A workflow is `name@version` with *integer*
  versions (schema v1 already types `version`), dense — the next version is
  exactly `max+1` (gaps are refused; they only ever signal a corrupt
  seed/DB) — and never reused, so "version N of X means exactly one
  document" holds globally and forever. `latest` is *computed, never
  stored*: the highest existing version of the name. No pointer row — a
  mutable "current" pointer is extra state that can drift from history
  (Ousterhout: don't add state without a consumer; Cordis ch08's
  single-version resolution). Refs: `name` and explicit `name@latest`
  float; `name@N` pins. Floating resolves **at run start** (pre-flight,
  stage-tagged `selection` like today), not at bind time — a daily batch
  run has no long-lived committed view to drift (Cordis ch05's
  committed/target machinery is for resident fibers). Floating stays
  auditable because every engine event and run report already records the
  `workflow` + `version` that ran (ticket 02's `_run_fields`):
  reproducibility comes from the log, not from caching.
- **Station bindings (rule fixed here; mechanics ticket 10).** A binding
  stores the ref string as given. Bind-time witnesses (definition, not
  use): a pinned ref must name an existing version, and the workflow must
  not be retired — a bad binding is refused when created, never discovered
  at 6am. Floaters re-resolve each run.
- **Immutability.** `workflow_versions` is INSERT-only — the repo exposes
  no update/delete for it (LogRepo idiom). A mistaken version is
  superseded by the next one, never corrected; even a never-run version
  keeps its number. The document must pass `require_valid` and its
  embedded `name`/`version` must equal the row's identity (ticket 01's
  "catalog row must match" discharged here); `(name, version)` is the
  primary key and re-creating an existing version fails loudly — never
  overwrite. The first version of a name implicitly creates the
  `workflows` row (one entry point; `workflow_created` vs
  `workflow_version_created` distinguish it in the log).
- **Who may create versions / actor tags.** Any surface (CLI, MCP tools,
  seed import) — but every mutating call takes a **required `actor`**
  (no default: a silently-defaulted agent edit is the mis-attribution
  that matters), vocabulary = `LogEntry.actor`'s `system | user | agent`,
  stored as `created_by` on the version row and logged
  (`workflow_created` / `workflow_version_created` / `workflow_retired` /
  `workflow_unretired`, detail `{workflow, version?}`). The seed-import
  and shipped-descriptor paths record `via: seed` / `via: shipped` in the
  log detail so origin is auditable without new columns.
- **Retire, never delete.** Retirement is name-level: `workflows.retired_at`
  (nullable flag), keeping `workflow_versions` strictly append-only (no
  flag there, no UPDATE ever). Un-retire is allowed and logged — the data
  was never gone. Delete is *not expressible* in the repo API at all.
  Per-version quarantine ("version 3 is known-bad") is deliberately not
  expressible in v1: the remedy for a bad version is the next version,
  and running an old one is a deliberate pin. Ousterhout ch8: don't add
  the knob until someone needs to set it.
- **What retirement does to stations.** A retired name refuses: new
  versions, new bindings, and run resolution (both pinned and floating —
  `CatalogError`, loud). An *existing* binding to it fails the run loudly
  at pre-flight with "workflow 'x' is retired (since …)", stage-tagged
  `selection` like any bad selection — **never a silent skip, never a
  silent fallback** to an older version (a silent behavior change is the
  one outcome the system refuses everywhere else, too). The fix is
  deliberate: rebind the station or un-retire. This is Cordis ch05's
  retire-vs-remove: retirement is a request that *surfaces* to dependents
  at their next boundary; it doesn't reach into them.
- **The engine stays catalog-blind.** `run_workflow` keeps taking a
  document — the second deep interface doesn't grow a catalog parameter.
  Enforcement lives at the resolution seam: `resolve(ref) -> validated
  document` in the catalog, raising `CatalogError` on missing/retired/
  malformed refs; ticket 05 wires `newsdesk morning` through it.
- **Diff.** Structural JSON, not a unified text diff (line diffs of
  pretty-printed JSON are noise): `diff_descriptors(a, b) -> list of
  {path, kind: added|removed|changed|order, before, after}` with RFC 6901
  JSON Pointer paths. Stages compare **keyed by stage name** (a mid-list
  insert doesn't cascade "changed" onto every later stage) and a reorder
  surfaces as one `order` change on `/stages` (order is meaningful — the
  interpreter is linear). Both documents must be valid v1 and share the
  name; cross-name diffs are refused loudly. The function is pure and
  module-level; CLI and agent tools are thin renderers of the same JSON —
  agent-readable by construction, human-readable via rendering.
- **Seed v2.** `format: 2` gate, refuse everything else (v1 precedent);
  the repo's seed file re-exports in ticket 12, old files live in git
  history. The `workflows` section carries **full descriptor documents**
  — they self-describe `name@version` (ticket 01), so no identity
  indirection is needed *within* workflows; the `source_keys`-style
  indirection is the later `stations` section's job (a station references
  its workflow by ref string, never by row id — ticket 12). Import is
  idempotent on `(name, version)`: absent → insert; present with a
  parsed-equal document → no-op (parsed equality, not bytes — whitespace
  is not data); present with a *different* document → loud `SeedError`
  (history was violated somewhere; never overwrite). Import applies
  retirement monotonically: `retired: true` in the file retires, and a
  stale seed can never *un*-retire (un-retire stays a deliberate local
  action). Export writes **full history** (every version, name+version
  order) so a fresh DB can serve stations pinned to old versions.
- **Validation on save AND on load.** On save: full `require_valid` +
  identity match + `(name, version)` uniqueness — nothing invalid ever
  enters. On load/resolution: re-validate. The honest Cordis framing:
  witnesses at definition discharge *consumer* obligations (plugins never
  pay — the engine's validated-document guarantee stands); the load-time
  re-validation is a **boundary check on the storage channel**, which
  newsdesk does not exclusively control (a hand-edited SQLite file, a
  restored backup, a newer writer emitting `format_version: 2`). The
  check is deterministic, local and free; failing loud beats silently
  running a wrong document.
- **Shipped-descriptor bootstrap.** The package's `default-morning@1`
  imports into the catalog idempotently on first use (actor=system,
  via=shipped); after import the DB row is the living data and the
  package file **freezes** — ticket 02's "the shipped @1 file is still
  living data" ends here. User edits create DB version 2; the package
  never gains files from user edits — the git round-trip is seed v2's
  job (standing decision).
- **API surface fixed for ticket 06** (`newsdesk/workflow/catalog.py`,
  `WorkflowCatalog(session)`, repo-layer idiom): `create_version(doc,
  actor)` (creates the name row at version 1), `get(name, version)`,
  `resolve(ref)` (the only resolution path; validates on load),
  `list()` (all names, incl. `retired_at`, latest version),
  `versions(name)` (full history; unknown name → loud),
  `retire(name, actor)` / `unretire(name, actor)`,
  `diff(name, va, vb)` over module-level `diff_descriptors`. Tables:
  `workflows{name PK, created_at, retired_at}`,
  `workflow_versions{(name, version) PK, document JSON, created_by,
  created_at}` — `create_all`, no alembic. Ticket 06's verb list gains
  `unretire` (see its Comments).
