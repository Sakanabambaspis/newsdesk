Type: grilling
Status: resolved
Blocked by: 01

## Question

Lock the schedule policy: the UTC cron time (buffered so typical Actions
delays still land before ~9am HKT), digest-window semantics when a run fires
late or a day is missed (skip vs catch-up), retry policy for failed runs,
and what the user sees when a day fails. Include the keep-alive answer for
the scheduled-workflow 60-day inactivity disable (what counts as activity,
and whether the daily run's own commits suffice).

## Answer

Decided 2026-09-18. Minimal machinery — nothing defensive until it's earned
its place:

- **Schedule: single fire, cron `7 23 * * *` UTC = 07:07 HKT daily.**
  Deadline is publish-by-08:30 HKT: 83 min of slack absorbs the median
  5–15 min delay (worst realistic ~30 min) plus the ~10 min pipeline run —
  expected publish 07:20–07:45.
- **No retry fire.** The user declined the second cron ("hold that off
  until it starts to get annoying"). Consequence accepted: a dropped fire
  means a silently missed day. The run keeps a cheap "today's episode
  already exists → exit" idempotency guard anyway, so a manual re-run
  (workflow_dispatch) after a failure can't double-publish — and the future
  retry fire, if ever added, needs no redesign.
- **Missed days: no catch-up.** Fixed rolling 24h window; a gap stays a
  gap.
- **Failure visibility:** GitHub's automatic workflow-failure emails are
  the only alert. No dead-man's switch. Same revisit trigger as the retry:
  when missed mornings actually annoy.
- **Keep-alive: none needed** (60-day disable is public-repo-only; private
  repo exempt). **Quota:** ~300 min/month against 2,000 free.
- **Manual override:** `workflow_dispatch` stays enabled for testing and
  backfilling a missed day by hand.
