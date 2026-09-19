# Agent tool surface

Type: grilling
Status: open
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
