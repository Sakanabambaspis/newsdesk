# GitHub Actions scheduled workflows: reliability for a daily morning pipeline

Research date: 2026-09-18. Sources: docs.github.com official docs; GitHub community
discussions for empirics (community numbers are anecdotal — GitHub publishes no SLA
or delay statistics for scheduled workflows).

## 1. Cron is UTC-only; 00:50 UTC = 08:50 HKT

Confirmed. Official wording: "By default, scheduled workflows run in UTC" (POSIX cron
syntax; shortest possible interval is "once every 5 minutes").
https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows

Hong Kong Time is UTC+08:00 year-round; Hong Kong has used no DST since 1979.
https://en.wikipedia.org/wiki/Hong_Kong_Time

So `50 0 * * *` fires at 08:50 HKT. A 9:00am HKT deadline equals 01:00 UTC.

## 2. How late do scheduled runs actually fire?

Official docs acknowledge both delay and loss: "The `schedule` event can be delayed
during periods of high loads of GitHub Actions workflow runs. High load times include
the start of every hour. If the load is sufficiently high enough, some queued jobs may
be dropped. To decrease the chance of delay, schedule your workflow to run at a
different time of the hour." (events doc, URL above). No numbers are given.

Community-reported numbers (no SLA exists; these are individual reports):

- One monitored setup: most runs ~5 min late, tail up to ~20 min.
  https://medium.com/average-coder/can-you-use-github-actions-for-monitoring-e9c6cfe79ef4
- Consistent 15–20 min lateness reported in
  https://github.com/orgs/community/discussions/158356
- Long thread of reports (~2025): ~30 min typical; several users report 1–4 h,
  some 7–10 h on bad days, and outright dropped runs ("they are no longer merely
  delaying the runs but dropping them entirely"); several note degradation over
  months. A GitHub staffer's diagnosis (relayed by a commenter): the drift is
  upstream of the runner queue — i.e., in GitHub's schedule dispatcher, so nothing
  on the workflow side fixes it. Same thread: "workflow_dispatch gets picked up
  near-instantly; it's specifically the schedule event that's deprioritized."
  https://github.com/orgs/community/discussions/156282

Realistic planning numbers (synthesis, anecdotal): median ~5–15 min late; regularly
20–30 min at congested times; worst case hours late or silently dropped with no
retry. Treat "fires within N minutes of cron" as unguaranteed.

## 3. The 60-day auto-disable policy

Exact wording: "In a public repository, scheduled workflows are automatically
disabled when no repository activity has occurred in 60 days."
https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/disabling-and-enabling-a-workflow
(same sentence also appears in the events doc, URL above).

- Scope: the docs state the rule for **public repositories only**. They say nothing
  about private or internal repos, and they do not define "repository activity".
- What counts as activity: undocumented. Empirically, commits reset the timer —
  a user reports workflows disabled "because of no active commits" even though the
  runs themselves fetched new data each day (runs ≠ activity):
  https://github.com/orgs/community/discussions/63146 . The popular
  `keepalive-workflow` pattern works by creating a (skip-CI) commit / API activity
  on a timer. Whether issues/comments reset it is not documented anywhere found.
- Re-enabling: Actions tab → select workflow → "Enable workflow"; or
  `gh workflow enable <workflow>`; or the REST API. Same docs page as above.
  There is no opt-out from the policy (feature request still open:
  https://github.com/orgs/community/discussions/86087 ).
- A repo fed only by its own workflow's commits: evidence suggests those daily
  commits DO count as activity and keep the schedule alive (that is exactly how
  keepalive workflows operate). Uncertainty: based on community behavior, not docs.
- For newsdesk's **private** repo: the documented policy does not apply, so the
  60-day disable should not bite. Caveat: separate, undocumented failure mode where
  a newly added schedule fires late or never registers on a new repo — verify the
  first scheduled run actually happens (reports:
  https://github.com/orgs/community/discussions/203822 ,
  https://github.com/orgs/community/discussions/202602 ).

## 4. Ways to get precise timing

### (a) External cron calling the GitHub API

- Endpoint: `POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches`
  (body: `ref`; `workflow_id` may be the file name). Workflow must have a
  `workflow_dispatch` trigger. "You can use this endpoint to manually trigger a
  GitHub Actions workflow run."
  https://docs.github.com/en/rest/actions/workflows?apiVersion=2022-11-28
- Alternative: `POST /repos/{owner}/{repo}/dispatches` (repository_dispatch).
  https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28
- Tokens — classic PAT: "OAuth tokens and personal access tokens (classic) need the
  repo scope" (both endpoints, same pages). Fine-grained PAT:
  workflow_dispatch needs repository permission **Actions: write**;
  repository_dispatch needs **Contents: write** (Metadata read is implicit).
  https://docs.github.com/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens
  Scope the PAT to the single repo and store it as a repo secret.
- Reliability: dispatch events bypass the schedule dispatcher and are picked up
  near-instantly per community reports (discussion 156282, above). API cost is
  trivial (PAT limit 5,000 req/h; we need 1/day). cron-job.org free tier: unlimited
  jobs, 1-minute resolution, email failure alerts, auto-deactivate after 25
  consecutive failures, but its own FAQ warns of "slight delays during peak hours"
  (and a 30 s HTTP timeout — irrelevant for a fire-and-forget POST).
  https://cron-job.org/en/faq/
- Trade-off: adds one third-party dependency and one managed secret. Dead-man's-switch
  alerting (cron-job.org's failure emails, or Healthchecks.io pinged by the workflow)
  is worth adding regardless of trigger path.

### (b) Self-hosted runner

You provide and maintain the machine ("you are responsible for updating the
operating system and all other software"); can be physical/virtual/container; jobs
may run up to 5 days; runner app auto-updates.
https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners

Not a fix for timing: the schedule queue is upstream of runners, so self-hosted
runners inherit the same delay (discussion 156282). To get precision you would run
local cron and call the dispatch API — at which point the runner is redundant.
A permanently-on machine (Pi/mini PC) + OS maintenance is real ongoing effort.
GitHub had announced a $0.002/min platform charge on self-hosted usage in private
repos (counting against included minutes) starting 2026-03-01, then postponed it:
https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/

### (c) Plain scheduled workflow with a buffer

Free, zero moving parts, but unguaranteed (see finding 2). Mitigations: avoid the
top of the hour (docs' own advice), schedule early enough to absorb a 30–60 min
tail, and consider firing the cron twice (e.g. two lines) with an idempotency guard
("skip if today's digest already exists") so a dropped first attempt is recovered.
This is the standard approach for non-hard deadlines.

Honest comparison: (c) covers a ~9am target almost every day; (a) is the only
low-effort path with near-instant, verifiable trigger times; (b) buys nothing here.

## 5. Actions minutes on private repos (free plan)

- Included quota: GitHub Free includes **2,000 Actions minutes per month** for
  private repositories ("for private repositories each GitHub account receives a
  quota of free minutes"; public repos' standard-runner usage is free and unbilled).
  https://docs.github.com/en/billing/concepts/product-billing/github-actions
- Multiplier: the legacy model was Linux 1x / Windows 2x / macOS 10x; the current
  billing page instead lists per-minute rates after the Jan 2026 price cut
  (Linux 2-core $0.006/min, Windows $0.010/min, macOS $0.062/min). The changelog
  confirms "the free usage minute quotas will remain the same" and describes the
  included quota as "over 33 hours of included GitHub compute" — i.e. 2,000 Linux
  minutes, so Linux remains effectively the 1x baseline.
  https://docs.github.com/en/billing/concepts/product-billing/github-actions and
  https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/
- A daily ~5-minute Linux job: 5 × 30 = ~150 Linux-minutes/month = ~7.5% of the
  2,000 quota. Comfortably inside, even with headroom for retry runs. (Legacy docs
  rounded each job's usage up to the nearest minute; current pages no longer state
  rounding — at most it adds <30 min/month here.)

## 6. Other material facts for scheduled workflows on private repos

- Scheduled workflows always run "on the latest commit on the default branch" —
  the pipeline workflow must live on the default branch, and cron changes only take
  effect once merged there. (events doc, URL in finding 1.)
- Hard limits: 6 h max per job, 35 days max per workflow run, 256 jobs per matrix;
  GitHub-hosted runners only. https://docs.github.com/en/actions/reference/limits
- Private-repo runs draw down the included minutes; larger (paid) runners are
  always charged even in public repos. (billing page, URL in finding 5.)
- GITHUB_TOKEN inside the run is limited to 1,000 requests/hour per repository —
  relevant if the collection step hits the GitHub API; external HTTP (news APIs,
  TTS) is unaffected. (limits page, above.)
- Scheduled runs can be dropped entirely under load with no notification (finding 2),
  so a silent-failure check belongs in the design regardless of trigger choice.

## Implications for newsdesk

- Deadline math: 9:00am HKT = 01:00 UTC. The proposed 00:50 UTC leaves only 10 min
  of margin; given a 5–15 min median delay and a 20–30+ min tail, that is too tight.
- Pick an off-hour cron well before the deadline, e.g. `7 0 * * *` (08:07 HKT) or
  `10 0 * * *` — avoid `:00` per the docs' own congestion advice.
- Default plan: plain scheduled workflow with that buffer, plus a two-line cron
  (e.g. also `32 0 * * *`) guarded by "skip if today's digest already built" to
  recover dropped runs. Cheap and usually sufficient.
- If timing proves unreliable, upgrade to an external trigger: cron-job.org (free)
  POSTing `workflow_dispatch` with a fine-grained PAT scoped to the repo with
  Actions: write, stored as a repo secret. Community evidence says dispatches start
  near-instantly. Keep the schedule trigger as fallback.
- Do not bother with a self-hosted runner: it does not fix the schedule delay
  (queue is upstream), and it adds a maintained always-on machine for no benefit —
  `ubuntu-latest` at ~150 min/month is far inside the 2,000 free minutes.
- 60-day disable is a non-issue for a private repo (documented for public only);
  if the repo ever goes public, the daily output commits from the pipeline itself
  should keep it active — but treat that as empirical, not guaranteed.
- Add a dead-man's-switch: last workflow step pings Healthchecks.io (or rely on
  cron-job.org failure emails if using path (a)) so a silently dropped or disabled
  schedule is noticed before the first missed 9am.
- Verify the first scheduled run actually fires after merging the workflow to the
  default branch; new schedules sometimes register late or not at all (anecdotal).
