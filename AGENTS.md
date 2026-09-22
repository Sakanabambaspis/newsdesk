# AGENTS.md

## Before planning or starting any effort — MANDATORY ROUTING

`.scratch/program/map.md` is this repo's **single source of todo across
efforts**. Do not plan around it.

- **Trigger**: you are about to plan new work — run `/wayfinder` or
  `/to-spec` for a new effort, write a spec/PRD, or create a new
  `.scratch/<effort>/` directory.
- **Required first act**: read `.scratch/program/map.md`, then **claim the
  effort ticket** under `.scratch/program/issues/` (`Status: claimed` +
  `Claimed:` date line) before opening or creating any effort map. One
  effort is active at a time. Starting a window whose `Blocked by:`
  predecessors are unresolved — or whose user gate has not been passed — is
  a program violation.
- **Not required**: ordinary ticket-level work inside the already-active
  effort (fixes, implementation, doc edits) — just work the effort's own
  tracker.
- **Gates are user-judged.** Never mark an effort ticket `resolved` without
  the deliverable pointer *and* the user's explicit gate verdict recorded in
  its `## Answer`.

Full conventions (claim discipline, stale-claim takeover, status
vocabulary): `docs/agents/issue-tracker.md`.

## Repo orientation

- Domain glossary: `CONTEXT.md` · ADRs: `docs/adr/` · Design: `docs/DESIGN.md`
- Prior planning efforts and their decision records: see the effort index in
  `.scratch/program/map.md`
