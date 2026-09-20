# Implement W5: MCP tools + dry-run

Type: task
Status: resolved
Blocked by: 13

## Question

(HITL build) Implement the tool surface once as host-neutral functions,
surfaced via TOOL_SPECS + HTTP routes + MCP tools per ticket-13 decisions.

Acceptance (measurable):

- all tools reachable from both HTTP (`/tools/...`) and MCP, described in TOOL_SPECS;
- end-to-end chat edit works: harness conversation → validated new workflow version stored → next scheduled run uses it;
- dry-run provably cannot publish (test asserts no publisher/wrangler invocation path);
- every mutation actor-tagged in the log; unknown tool names still degrade to the 501 stub.

## Answer

Resolved 2026-09-21.

**Built exactly on ticket 13's spec.** The twenty tools live once in
`newsdesk/agents/tools.py` (`fn(session, settings, ...) -> dict`), each
carrying its spec (name, description, args, `http` route, `mutating`
flag) via the `@tool` decorator, so `protocol.TOOL_SPECS` is *generated*
(`... + workflow_tool_specs()`); HTTP (`api/app.py`, `via="http"`) and
MCP (`mcp_server.py`, `via="mcp"`) stay hand-written thin adapters; a
POST catch-all joins the GET stub so unknown names 501 on both methods.
Validation-on-save moved fully into the repo layer: a new
`workflow/bindings.py` exports `validate_bindings(descriptor,
registries, *, defaults)` — the engine's static pre-flight (pin
uniqueness, pinned-plugin existence, required select `rubric` param,
closed params key sets, `CHECK_REQUIRES` binding guards) extracted from
`_resolve_plugins` and now called by BOTH the engine (with the settings
knobs as `defaults`) and `WorkflowCatalog.create_version` (settings-
blind: unpinned render/publish are re-checked at run start; unpinned
compose resolves the registry default statically). The 64 KiB
serialized-document cap is `require_document_size` in `catalog.py`,
inherited by both catalogs, so CLI/seed/tools refuse identically. The
rubric diff exists: `diff_documents(a, b, *, list_key, label_of)` is the
generalized core, `diff_rubrics` keys it on `dimensions` (reorder = one
`/dimensions` order entry — order is behavior for weight
renormalization), `RubricCatalog.diff` exposes it. `run_workflow` takes
no publish and no dry-run argument and passes `dry_run=True`
unconditionally; the summarized report carries per-stage
`{type, plugin, counts}` + the select's `{ids, totals}` + the sidecar
path. The acceptance is pinned: sync test (`test_agent_tools_surface.py`)
asserts registry↔specs↔HTTP↔MCP↔501 and that the check library, the
binding map and the schema's closed check set agree; the chat-edit test
stores v2 through the tool and the next run (float resolve) uses it with
`actor="agent"`/`via` in the log; the dry-run proof patches both
publishers *and* `_wrangler_deploy` to raise, asserts a complete
`dry_run` report, an empty `publish_dir`, and — structurally — no
publish/dry-run parameter in the signature.

**Review-found regression, fixed at the root:** the extraction initially
let params on a `notify` stage pass silently (the old `_allowed_param_keys`
answered `frozenset()` there; the new code skipped registry-less types).
Restored and pinned: any param on notify fails the save gate and
pre-flight.

**Deliberate deviations from ticket 13's table** (each documented in the
tool description): `create_rubric` has **no `dry_run` param** — §5
defines the smoke only for workflows (a rubric "smoke" that runs nothing
would be a lying boolean; `score_preview` is how you try a rubric);
`update_station` requires `workflow` (the repo's full-document update
semantics need the non-empty ref); `diff_workflow`/`diff_rubric` return
`{name, from, to, changes}` rather than a bare list (every tool returns a
dict); `score_preview` adds `window_hours`/`items_in_window`/
`verdict_method` context alongside the spec'd shape; mutating stations
return `{**row, actor, via}` for origin parity with the catalogs.

Code: `newsdesk/agents/tools.py`, `newsdesk/workflow/bindings.py`,
`newsdesk/workflow/catalog.py` (cap + gate + `diff_documents`),
`newsdesk/workflow/rubric_catalog.py` (`diff_rubrics` + `diff`),
`newsdesk/workflow/engine.py` (delegates static validation),
`newsdesk/agents/protocol.py`, `newsdesk/api/app.py`,
`newsdesk/mcp_server.py`; tests `test_agent_tools.py`,
`test_agent_tools_surface.py`, plus pins in `test_workflow_catalog.py`,
`test_workflow_rubric_catalog.py`, `test_api.py`, `test_mcp.py`. Suite:
467 green.
