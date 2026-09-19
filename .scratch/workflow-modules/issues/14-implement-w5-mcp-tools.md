# Implement W5: MCP tools + dry-run

Type: task
Status: open
Blocked by: 13

## Question

(HITL build) Implement the tool surface once as host-neutral functions,
surfaced via TOOL_SPECS + HTTP routes + MCP tools per ticket-13 decisions.

Acceptance (measurable):

- all tools reachable from both HTTP (`/tools/...`) and MCP, described in TOOL_SPECS;
- end-to-end chat edit works: harness conversation → validated new workflow version stored → next scheduled run uses it;
- dry-run provably cannot publish (test asserts no publisher/wrangler invocation path);
- every mutation actor-tagged in the log; unknown tool names still degrade to the 501 stub.
