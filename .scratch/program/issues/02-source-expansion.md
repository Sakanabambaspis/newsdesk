# 02 — Source expansion (email / forums / newsletters / messages)

Type: task (effort window)
Status: open
Blocked by: 01

## What this effort is

A wayfinder map deciding how newsdesk collects and briefs non-news sources:

- **Station topology** — separate briefing per source class vs. merged. The
  W4 machinery exists (station rows, per-station feeds, scoped digest, CI
  matrix); this decides the configuration and each station's editorial
  policy.
- **Collection** — which fetchers to ship, in which order. Email (IMAP)
  exists; `forum` / `newsletter` / `html` / `sitemap` / `api` are staged
  kinds with no fetcher (`error:no-fetcher` at collect time).
- **Selection** — per-station rubric policy, designed with effort 01's
  findings as binding input (what selection gets right and wrong today).
- The **email-digest-audio** fog item from the morning-audio map graduates
  here (selection/importance rules over the mailbox, privacy posture for
  personal mail in CI).

## Consumes

The briefing-quality findings doc from effort 01 — required before this
window opens.

## Deliverable

Decision-complete map → `/to-tickets` → implementation windows.

## Gate (user-judged)

Fetchers shipped, stations configured, feeds live and subscribed on devices.

## Kickoff (fresh session)

> Run /wayfinder for source expansion: station topology for non-news sources
> (separate vs. merged briefings), which fetchers to ship, per-station
> rubric/editorial policy, and the graduating email-digest fog. Binding
> input: the briefing-quality findings doc. Program context:
> `.scratch/program/map.md`.
