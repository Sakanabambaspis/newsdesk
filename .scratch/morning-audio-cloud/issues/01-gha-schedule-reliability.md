Type: research
Status: resolved
Blocked by:

## Question

Can an unattended GitHub Actions schedule deliver the morning audio before
~9am HKT every day, and what schedule/failure policy follows from the
evidence?

Sub-questions: How late do scheduled runs actually fire (typical and worst
case)? UTC mapping for HKT; what cron time leaves buffer before 9am? The
60-day repository-inactivity disable for scheduled workflows — exact policy,
what counts as activity, how a repo whose only traffic is its own daily
workflow stays alive. Actions minutes on a private free-plan repo for a daily
~5-minute job. External-trigger (cron-job.org → repository_dispatch) and
self-hosted-runner alternatives, judged against the accepted
"by ~9am is fine" slack.

Findings: `docs/research/gha-schedule-reliability.md`

## Answer

Yes — with a buffered cron, comfortably. Evidence in
`docs/research/gha-schedule-reliability.md` (185 lines, all cited).

- Cron is UTC-only; HKT is UTC+8 with no DST, so 08:50 HKT = 00:50 UTC
  exactly. But 00:50 leaves only 10 min of margin against a 9:00 deadline —
  too tight: median schedule delay is ~5–15 min, 20–30 min common at
  congested times, and queued runs can be **dropped entirely** (docs admit
  delays; drops are community-observed).
- Recommended: an off-hour cron like `7 0 * * *` (08:07 HKT) plus a second
  fire ~00:32 UTC guarded by an idempotency check ("skip if today's episode
  exists"), and a dead-man's-switch ping if nothing published by ~08:45.
  External cron-job.org → `workflow_dispatch` (fine-grained PAT with
  **Actions: write**) is the precision fallback, only if the schedule path
  proves unreliable.
- **The 60-day scheduled-workflow disable applies to PUBLIC repositories
  only** (docs scope it explicitly) — our private repo is exempt. No
  keep-alive machinery needed.
- Quota: free plan gives 2,000 min/month for private repos at the Linux 1x
  multiplier; a daily ~5-min job ≈ 150 min/month ≈ 7.5% of quota.
- Self-hosted runners don't fix delays (delay is upstream of runners) — ruled
  out.
