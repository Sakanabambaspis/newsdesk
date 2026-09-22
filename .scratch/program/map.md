# Program map: briefing platform

Status: the frontier is effort **01 (briefing-quality diagnosis)** — open,
unclaimed. Compute it the usual way: first by number, unblocked, unclaimed.
Program ticket 04 (episode archive) is claimed 2026-09-22 by direct user
order — a compact task, not an effort window; it needs no effort map.

This directory is the repo's **single source of todo across efforts**. Any
session about to plan or start work reads this map first and **claims the
effort ticket before opening or creating any effort map** (`Status: claimed`
+ a `Claimed:` date line). One effort is active at a time. Full mechanics —
status vocabulary (`open | claimed | resolved`), claim races, stale-claim
takeover after 7 idle days — are defined in `docs/agents/issue-tracker.md`
(Program level), which `AGENTS.md` at the repo root routes every planning
session to.

## Destination

The briefing platform, delivered as three gated windows: (1) the morning
briefing is diagnosed against evidence and its top failure causes fixed to a
bar the user accepts; (2) newsdesk collects and briefs non-news sources
(email / forums / newsletters / messages) as stations with explicit
per-station editorial policy; (3) a briefing-designer agent turns chat
conversations into source/rubric/workflow/station changes behind approval
gates, with source discovery, vetting, and injection-safe mutation policy.

## Notes

- Standing constraints: free-only budget; credentials never in DB or logs
  (DESIGN.md §18); polite fetching only (DESIGN.md §5.2) — no new scraping
  postures; provenance-first, no opaque filtering (ADR 0001).
- Substrate already shipped: **stations** (W4 — per-station feeds, scoped
  digest, CI matrix) and the **agent tool surface** (W5 — `create_workflow`
  validated-on-save, rubric/station CRUD, `add_source`, dry-run-only agent
  runs, actor-tagged mutations); see
  `.scratch/workflow-modules/issues/13-agent-tool-surface.md`.
- Gates are **user-judged and mechanically enforced**: an effort ticket only
  becomes `resolved` with the deliverable pointer *and* the user's explicit
  gate verdict in its `## Answer`. The `Blocked by:` edges therefore cannot
  be crossed without the user having passed the gate.
- Hand-off artifacts are named on each effort ticket (`Consumes` /
  `Deliverable`); a window that starts without its upstream artifact is a
  program violation — go back and finish the upstream gate.

## Decisions so far (effort index)

- [morning-audio-cloud](../morning-audio-cloud/map.md) — done 2026-09-18;
  10/10 decisions resolved; implementation handed off below.
- [morning-audio-impl](../morning-audio-impl/) — shipped; live on the Actions
  schedule (see `docs/morning-actions.md`).
- [workflow-modules](../workflow-modules/map.md) — W1–W5 shipped on main;
  ticket 15 (elicitation playbook) intentionally parked: it graduates into
  effort 03 rather than being resolved in isolation.

## Not yet specified

- Effort-internal questions (rubric policy details, fetcher targets,
  guardrail mechanics) belong to each effort's own map when its window opens
  — never planned ahead here.

## Out of scope

- Loosening the politeness/posture for scraping — separate effort, per the
  morning-audio map.
- Native mobile app; any paid plan — standing user decisions.
