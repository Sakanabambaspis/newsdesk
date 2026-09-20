# Agent tool surface

Type: grilling
Status: resolved
Blocked by: 06, 09, 10

## Question

Decide the exact tool surface for chat-driven customization:

- tool list: workflow/rubric/station CRUD, catalog search + diff, dry-run, score-preview — names, args, returns;
- dry-run semantics: engine with publish/notify skipped; what it may write (log only); proof it cannot publish;
- MCP result-size etiquette: truncation/summary rules for large descriptors so tool results stay context-sized;
- validation-on-save (schema + checks + dry-run smoke) before a version is stored;
- actor tagging (`actor=agent`) on every mutating call;
- TOOL_SPECS entries shared by HTTP + MCP (describe once — the established pattern).

Answers are the build spec for ticket 14.

## Answer

Resolved 2026-09-20.

### 1. Shape: one implementation, two thin adapters, one description

Every tool is **one host-neutral function** in a new module
`newsdesk/agents/tools.py` — signature
`fn(session, settings, ...) -> dict`, no surface knowledge, no actor
argument. `TOOL_SPECS` (already the single description) is *generated from
that registry*, not hand-maintained beside it: each implementation carries
its spec (name, description, args, mutating flag) as module-level metadata,
so a tool cannot exist without its description. The HTTP route in
`newsdesk/api/app.py` and the MCP function in `newsdesk/mcp_server.py` stay
hand-written (FastAPI and FastMCP both need concrete typed signatures to
produce their schemas — a `**kwargs` generator would hand harnesses an
untyped blob, which is the opposite of the point) but are *thin*: parse,
call the one implementation, return.

"Describe once" is then **enforced, not promised**, by one sync test:
every registry entry appears in `TOOL_SPECS`, every `TOOL_SPECS` entry has a
registry implementation or an explicit `status` of `planned`, every `wired`
spec is reachable on both surfaces (HTTP route + MCP tool name), and no
implementation is orphaned. The existing 501 stub stays the fallback for
unknown names (ticket 14's acceptance).

### 2. Tool list (names, args, returns)

Naming follows the surface's existing verb-first style (`list_sources`,
`add_source`, `create_digest`). Every read returns a summary; every
mutating tool returns the stored document **plus** its origin
(`{..., "actor": "agent", "via": "mcp"}`) so a harness can verify what it
just did without reading the log.

**Workflows** (`WorkflowCatalog`):

| tool | args | returns |
|---|---|---|
| `list_workflows` | `query?` (case-insensitive match on name + stage/plugin names), `include_retired?=false`, `limit?=50` | `{items:[{name, latest, retired_at, stages, created_at}], total, truncated}` |
| `get_workflow` | `ref` (`name`, `name@latest`, `name@N`), `version?` | the full validated document + `{ref, resolved_version, retired}` |
| `create_workflow` | `document` (the whole descriptor), `dry_run?=false` | `{name, version, actor, via, validation:{schema, bindings, smoke?}}` |
| `diff_workflow` | `name`, `from`, `to` (ints) | the `diff_descriptors` list (RFC 6901 paths, `added/removed/changed/order`) |
| `retire_workflow` / `unretire_workflow` | `name` | `{name, retired_at}` (both logged; neither is a delete) |

**Rubrics** (`RubricCatalog`, same conventions):

| tool | args | returns |
|---|---|---|
| `list_rubrics` | `limit?=50` | `{items:[{name, latest, retired_at, dimensions}], total, truncated}` |
| `get_rubric` | `ref`, `version?` | the full validated rubric document |
| `create_rubric` | `document`, `dry_run?=true` | `{name, version, actor, via}` |
| `diff_rubric` | `name`, `from`, `to` | the same diff shape, order-sensitive on `dimensions` |
| `score_preview` | `rubric` (a ref **or** an inline draft document), `limit?=10`, `hours?=24` | per-item `{id, total, outlets?}` + per-dimension `{name, score, reason}`, `method`, `llm_error`, `rubric_ref`, `considered` |
| `retire_rubric` / `unretire_rubric` | `name` | `{name, retired_at}` |

`score_preview` is the elicitation loop's core (ticket 15): it takes a
*draft* rubric (inline, unstored) or a stored ref, scores the **current**
candidate set (`candidate_items`, the same window/cap/filter/rank the
strategies use), and writes nothing outside the log — no collection, no
catalog row. Rubric reasons are stored outputs, and the preview is where
they are cheapest to read.

**Stations** (`StationRepo`):

| tool | args | returns |
|---|---|---|
| `list_stations` | `include_retired?=true` | `{items:[{name, workflow_ref, watchlist_id, path_segment, retired_at}], total}` |
| `get_station` | `name` | the row + the resolved feed identity (`resolve_identity`) and scope summary |
| `create_station` | `name`, `workflow`, `watchlist?`, `path_segment?`, `description?`, `feed?{title,description,author,category,language,owner_email}` | the stored row |
| `update_station` | `name`, `workflow?`, `watchlist?`, `description?`, `feed?` | the stored row |
| `retire_station` / `unretire_station` | `name` | `{name, retired_at}` |

`create_station`/`update_station` expose **no** `path_segment` edit on an
existing row and **no** delete: the repo's immutability rule (enclosure URLs
are permanent) is the API surface, and the tools inherit it rather than
re-check it. A retired station's feed is still deployed — retirement refuses
*runs*, never the archive.

**Runs** (see §3):

| tool | args | returns |
|---|---|---|
| `run_workflow` | `ref`, `station?`, `date?`, `verbose?=false` | the emission-free run report (§3), summarized by default |

### 3. Dry-run: the tool surface can only dry-run

`run_workflow` has **no publish parameter at all** — not a bool defaulting
to true (a caller could flip it), the argument does not exist. The tool
passes `dry_run=True` unconditionally, so ticket 14's "provably cannot
publish" is structural, not a configuration. An agent that wants a live
episode asks the owner to dispatch the schedule; publishing outward-facing
content is exactly the class of action a chat tool must not do on its own.

Semantics, made explicit because the ticket's phrase "log only" is not what
the engine does today and pretending otherwise would be a lie in the docs:

- **The emission boundary is the guarantee.** `run_workflow(dry_run=True)`
  breaks out of the stage loop *before dispatching the publish stage*, so
  publish and notify — and therefore every publisher, the wrangler
  subprocess, the feed/archive, and `publish_dir` itself — are unreachable
  from a dry run. The `archive_intact` check and the idempotency guard are
  skipped, and the run's outcome is `dry_run` (logged as such).
- **Writes:** a dry run writes what a real run writes *before* that
  boundary — collected items (idempotent, deduplicated, already the
  database's normal state) and the compose sidecar under the run's own
  `<morning_dir>/[<station>/]<date>/` layout (which the real run then
  overwrites) — plus its log entries. It never writes to `publish_dir`,
  the Pages project, or the catalog.
- **Refusing the stronger promise, deliberately.** "Log only" would need a
  second collection path that persists nothing, i.e. a code path the real
  run never exercises, exercised only by previews — the one path guaranteed
  to rot. A preview that sees a different candidate set than the run is
  worse than a preview that touches the item table. The guarantee worth
  having (never emits, never touches the archive) is total here.
- **Proof** (ticket 14's acceptance): a test that monkeypatches every
  publisher and `_wrangler_deploy` to raise, and asserts the tool returns a
  complete report; plus a test that `publish_dir` contains no file the dry
  run could have written.

**Report shape:** the engine's report is returned, summarized by default —
per stage `{type, plugin, outcome}` plus counts (sources, items in window,
in pack, chunks, seconds) and the select's `{ids, totals}` — with `verbose`
returning the full payloads. Bulk data (per-item reasons, item cards, the
script text) is *deliberately not* in the tool result: it is in the sidecar
(whose path the report carries) and the log. A tool result is a receipt, not
a data dump.

### 4. Result-size etiquette

1. **Documents** (workflow, rubric) are returned whole: v1 descriptors are
   ~1 KB and the schema bounds their shape. To keep that true, `create_*`
   refuses a serialized document over **64 KiB** — a boundary check at the
   repo layer (protects the log, the DB, and every future tool result).
2. **Lists** return summaries under a `{items, total, truncated}` envelope:
   default `limit` 50, hard max 200. Never full documents, never full
   history in one call (`diff` and `get` are how you drill in).
3. **Score previews**: `limit` default 10, hard max `MAX_LLM_ITEMS` (30);
   per-dimension reasons clipped to 200 chars with the total and the
   dimension score kept exact; `llm_error` is always present in the result
   (including `null`) so a mechanical fallback is never silent.
4. **Run reports**: summarized by default, `verbose` opts into the raw
   payloads; the sidecar path is always returned so a harness that needs the
   script text reads a file instead of a context window.
5. No tool returns a stack trace or an internal exception type: failures
   come back as `{error, hint?}` (the MCP server's existing containment) with
   the exact message the repo raised — the messages are written for humans.

### 5. Validation-on-save

The gate lives **in the repo layer** (`create_version`), so every surface —
CLI, seed, tool — inherits it identically; the tools add nothing of their
own:

1. **Schema** (`require_valid` / `require_valid_rubric`) — already there, and
   it already carries the closed check-name set with strict per-check params
   (`schema._CHECK_PARAM_KEYS`), so "unknown check" and "wrong params for a
   known check" are save-time failures today.
2. **Bindings** — the engine's pre-flight validators: stage-type binding
   (`_CHECK_REQUIRES`, so a check can never bind before the stage that
   provides its artifact), closed plugin param key sets
   (`_validate_stage_params`), and plugin existence / triple coherence.
   These are pure functions of the document and the registries; ticket 14
   extracts them from `engine._resolve_plugins` into a public
   `validate_bindings(descriptor, registries)` that both the engine and the
   catalog call — one implementation, two callers, no drift.
3. **The smoke (opt-in).** `create_workflow(document, dry_run=true)` runs
   the candidate document once through `run_workflow(..., dry_run=True)`
   against the current database *before storing it*, and refuses the save
   on any stage failure — the caller's explicit request, because a smoke
   collects (network + tokens) and a save must never surprise you with
   that. The default is static validation only. Documented in the tool
   description, which is what the harness reads.

Rubric refs are **not** resolved at save (a workflow may pin a rubric that
is created in the same conversation, and resolution already happens at
run pre-flight with a loud failure) — recorded so ticket 14 does not
"helpfully" add it.

### 6. Actor tagging

Every mutating tool calls the repo with `actor="agent"` **hard-coded in the
implementation** and `via` supplied by the adapter (`"mcp"` or `"http"`);
no tool takes an `actor` argument, so a harness cannot forge `user` or
`system`, and every mutation lands in the immutable log as
`<repo action>` with `{via, actor:"agent"}` exactly as the CLI's
`actor="user", via="cli"` does. Reads take no actor and write no log entry.
The sync test in §1 asserts every `mutating: true` implementation calls a
repo write — by construction (they wrap one) — and the log side is already
pinned per repo by the catalog/station tests.

### 7. What this ticket deliberately does not decide

- **CLI parity.** No new `newsdesk workflow` verbs: the CLI stays the
  human/ops surface (`create/get/list/history/diff/retire/unretire`), the
  tools are the chat surface. Two surfaces with the same verbs is how they
  drift; the shared repo layer is the contract.
- **Rubric catalog storage mechanics** — landed at ticket 09; the tools add
  no tables.
- **A rubric `diff_descriptors` generalization** (the order-sensitive key is
  `dimensions`, not `stages`): ticket 14's build detail, one parameter.
- **The elicitation playbook itself** (where it is documented, what the
  harness-side skill says) — ticket 15.
