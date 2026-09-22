# 03 — Briefing-designer agent

Type: task (effort window)
Status: open
Blocked by: 02

## What this effort is

A wayfinder map for the workflow-designing workflow — the agent that turns
chat into a working briefing:

- **Elicitation** — chat-driven "what should the briefing focus on" →
  concrete parameters. workflow-modules ticket 15 (elicitation playbook)
  graduates here as the seed ticket.
- **The design loop** — agent drafts the artifact set (sources, watchlist
  terms, rubric version, station binding, workflow descriptor) → validates →
  trial-collects → self-evaluates → proposes; the user approves the live
  switch. Extends the shipped posture: agent runs dry-run-only, mutations
  versioned and actor-tagged.
- **Source discovery + vetting** — finding candidate sources and proving a
  candidate worth subscribing before it pollutes the briefing (trial
  collection, quality floor, politeness/robots compliance, DESIGN.md §5.2).
- **Injection safety** — a page that talks the agent into registering
  attacker feeds is injection-to-persistence; discovery-derived mutations are
  quarantined until user-confirmed, with the actor-tagged log as audit trail.
- **Self-evaluation** — effort 01's failure catalog is the criteria the
  agent uses to judge its own designs.

## Consumes

Effort 02's fetcher inventory and station/rubric policy (the agent's action
vocabulary), and effort 01's failure catalog.

## Deliverable

Decision-complete map → `/to-tickets` → implementation windows.

## Gate (user-judged)

A briefing designed end-to-end by the agent from a chat conversation and
shipped after explicit user approval, with no unapproved live mutations.

## Kickoff (fresh session)

> Run /wayfinder for the briefing-designer agent: chat-driven elicitation of
> briefing focus, agent-authored source/rubric/workflow/station changes
> behind approval gates, source discovery + vetting loop, and injection-safe
> mutation policy. Binding context: the W5 tool surface
> (`.scratch/workflow-modules/issues/13-agent-tool-surface.md`), the
> source-expansion map's decisions, and the briefing-quality findings.
> Program context: `.scratch/program/map.md`.
