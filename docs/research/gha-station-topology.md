# CI fan-out for multi-station scheduled runs: loop, matrix, or per-station workflows

Research date: 2026-09-19. Sources: docs.github.com official docs, fetched on the date
(URLs inline); repo context: `docs/research/gha-schedule-reliability.md` (cron delay and
recovery pattern), `docs/morning-actions.md` (runbook: one `7 23 * * *` UTC cron,
~10-minute pipeline, publish-by-08:30 HKT, failure email as the only alerting), and
`.github/workflows/morning.yml` (one `morning` job, workflow-level
`concurrency: morning` with `cancel-in-progress: false`, `timeout-minutes: 45`, seed
step + `newsdesk morning --json`).

Question: one scheduled run must produce N station feeds — same `newsdesk` pipeline,
different parameters, N small (2–5). Fan out by (a) a single job looping stations,
(b) a matrix strategy job, or (c) per-station workflow files?

## 1. Matrix: one job definition, N parallel jobs

- "Use `jobs.<job_id>.strategy.matrix` to define a matrix of different job
  configurations. A job will run for each possible combination of the variables."
  https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/running-variations-of-jobs-in-a-workflow
  and https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
- Jobs "run in parallel by default" (syntax reference, `jobs` section), and matrix
  parallelism is explicit: `strategy.max-parallel` — "By default, GitHub will maximize
  the number of jobs run in parallel depending on runner availability." So total wall
  time ≈ one leg, not the sum over stations, subject only to account-level job
  concurrency (finding 5).
- Matrix values are plain data (here: station ids) readable everywhere as
  `${{ matrix.station }}` — in env maps, step conditionals, and job-level concurrency
  groups (finding 3).

## 2. Failure isolation inside one run: `fail-fast` — and the `continue-on-error` alerting trap

- The matrix default cancels siblings: "If `jobs.<job_id>.strategy.fail-fast` is set
  to true or its expression evaluates to true, GitHub will cancel all in-progress and
  queued jobs in the matrix if any job in the matrix fails. **This property defaults
  to true.**" (syntax reference). A bare matrix does NOT isolate stations — one
  station's LLM/TTS/publish failure would cancel the other stations' in-flight legs.
- The documented isolation lever is `strategy.fail-fast: false`: each leg then lives
  or dies alone.
- `jobs.<job_id>.continue-on-error: true` also keeps siblings running ("other jobs in
  the matrix will continue running even if the job with continue-on-error: true
  fails"), but it "Prevents a workflow run from failing when a job fails. Set to true
  to allow a workflow run to pass when this job fails." (syntax reference). A run that
  "passes" no longer matches the failure-notification condition (finding 6) —
  `continue-on-error` silently eats the failure email, which is this repo's only
  alerting channel.
- Lever of record: **`fail-fast: false` and no `continue-on-error`.** A failed leg
  then fails the run → the failure email fires — while sibling stations are unaffected.

## 3. Concurrency groups: workflow level vs job level, matrix context allowed

- Both levels exist. Workflow-level `concurrency` gates whole runs;
  "You can use `jobs.<job_id>.concurrency` to ensure that only a single job or
  workflow using the same concurrency group will run at a time."
  https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/using-concurrency
- Job-level groups take expressions: "A concurrency group can be any string or
  expression. Allowed expression contexts: github, inputs, vars, needs, strategy, and
  matrix." (workflow syntax reference) — so `group: morning-${{ matrix.station }}` is
  supported for per-station serialization across runs.
- Queue semantics (no `cancel-in-progress`): at most one running + one pending; "any
  existing pending job or workflow in the same concurrency group will be canceled and
  the new queued job or workflow will take its place. To also cancel any currently
  running job or workflow in the same concurrency group, specify
  `cancel-in-progress: true`." (concurrency page) — exactly today's `morning` group
  behavior. Legs of the same run never contend with each other, so adding a matrix
  requires no change to the workflow-level group.

## 4. Timeouts are per job — the matrix relieves the loop's budget squeeze

- "`jobs.<job_id>.timeout-minutes` — The maximum number of minutes to let a job run
  before GitHub automatically cancels it. Default: 360." (workflow syntax reference).
  Each matrix leg is its own instance of the job, so every station gets the full
  45 minutes. In the loop topology the same line is one shared budget for all N
  stations.

## 5. Hard limits and free-plan concurrency

- "A job matrix can generate a maximum of 256 jobs per workflow run"; "Each job in a
  workflow can run for up to 6 hours of execution time"; 35 days per workflow run.
  https://docs.github.com/en/actions/reference/limits
- Concurrent jobs on standard GitHub-hosted runners: Free plan — 20 total concurrent
  jobs (same limits page). N = 2–5 legs all start simultaneously; the cap is
  irrelevant at this scale.

## 6. Failure notifications: run-level, and who receives them

- "If you enable email or web notifications for GitHub Actions, you'll receive a
  notification when any workflow runs that you've triggered have completed. The
  notification will include the workflow run's status (including successful, failed,
  neutral, and canceled runs). You can also choose to receive a notification only when
  a workflow run has failed."
  https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs
- "Notifications for scheduled workflows are sent to the user who initially created
  the workflow. If a different user updates the cron syntax, in the schedule event in
  the workflow file, subsequent notifications will be sent to that user instead."
  (same page)
- Granularity is per run, not per job: one matrix run failing = one email; the failed
  leg(s) are visible in the run's UI. Per-station workflow files are the only topology
  that changes that (N independent emails).
- Recovery knobs, documented: "Re-run failed jobs" / `gh run rerun --failed`, max 50
  re-runs per run. https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/re-running-workflows-and-jobs
  newsdesk needs none of them: the idempotency guard (per station/date) means any
  fresh dispatch — or the second cron line of the two-line recovery pattern in
  `gha-schedule-reliability.md` finding 4(c) — no-ops published stations and rebuilds
  only the missing ones.

## 7. Reusable workflows (`workflow_call`) — the dedupe path if stations become files

- Same-repo callers use `uses: ./.github/workflows/<file>` with `with:` inputs and
  `secrets:`/`secrets: inherit`; "Matrix strategies can call reusable workflows,
  passing matrix values as inputs"; nesting is capped ("You can connect a maximum of
  ten levels of workflows"); reusable files must live flat in `.github/workflows`
  ("Subdirectories of the `workflows` directory are not supported").
  https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows
- So option (c) need not duplicate YAML: N thin scheduled wrappers calling one
  reusable workflow. It still costs N schedule registrations (finding 8).

## 8. Schedule-delay interaction (with gha-schedule-reliability.md)

- Delay and drop are per scheduled run, upstream of runners (existing doc, finding 2).
  One cron = one delay/drop event, **correlated across stations**: all stations start
  late together, or all are dropped together. The matrix changes nothing about trigger
  behavior. Per-station workflow files multiply registrations — each individually
  subject to delay, drop, and the new-schedule flakiness (existing doc, finding 3
  caveat) — and fragment recovery into N schedules.
- Wall-time math under delay: slack is 83 min (23:07 → 00:30 UTC deadline) minus the
  ~10-minute pipeline, so ~73 min of tolerated lateness for a parallel fan-out. The
  loop spends N × pipeline serially: at N = 5 that is ~50 min — already past the
  45-minute job timeout with zero delay — and a cron only ~25 min late pushes publish
  past 08:30 HKT. Matrix wall time stays ≈ single-leg for any N inside the
  concurrency caps (finding 5), so tolerated lateness is independent of station count.
- The two-line cron recovery pattern composes with all three topologies, but with a
  matrix one extra run heals every station; with per-station files each dropped
  station needs its own recovery trigger.

## 9. Cost, and why per-station secrets are a non-issue here

- Total Actions minutes are ≈ invariant across topologies (each station runs the
  pipeline once); the matrix repeats setup per leg (checkout, pip, wrangler, seed — a
  few minutes). Estimate at N = 5: ~50 leg-min/day ≈ 1,500 min/month (matrix) vs
  ~1,260 (loop) — both inside the 2,000 included free-plan minutes (existing doc,
  finding 5), and well under the 20-job concurrency cap (finding 5).
- Secrets/vars are shared env (one Cloudflare project; per-station token path segments
  are computed by the CLI from the station id), so the matrix needs only
  `NEWSDESK_STATION: ${{ matrix.station }}` — no dynamic secret-name lookup. (`format`
  is a documented expression function,
  https://docs.github.com/en/actions/reference/workflows-and-actions/expressions, but
  nothing here needs it; keeping station params in plain vars/data avoids the whole
  question.)

## Options compared

### (a) Single job looping stations

- Pros: setup runs once; one concurrency group; one email; smallest YAML diff.
- Cons: wall time scales with N — past the 45-minute job timeout around N ≈ 4–5 and
  through the 08:30 deadline slack sooner (finding 4, finding 8); failure isolation
  must be hand-rolled in shell (per-station error capture, aggregate exit code) where
  Actions provides it documented for matrices; one shared timeout budget; without the
  shell gymnastics, one station's failure kills later stations' episodes; failure
  visibility = grep the log.

### (b) Matrix over station ids — recommended

- Pros: wall time flat in N (findings 1, 5); documented failure isolation via
  `fail-fast: false` (finding 2); per-leg 45-minute budgets (finding 4); failure email
  preserved by omitting `continue-on-error` (findings 2, 6); one cron, one
  registration, unchanged `morning` concurrency semantics (finding 3); adding a
  station = editing one list.
- Cons: setup repeated per leg — roughly +20% minutes at N = 5 (finding 9); alerting
  is run-level, leg detail lives in the run UI not the inbox (finding 6); a dropped
  scheduled run drops all stations at once (mitigated by the two-line cron, which
  heals all stations in a single re-run, finding 8).

### (c) Per-station workflow files (optionally thin wrappers over one `workflow_call` workflow)

- Pros: isolation by construction (separate runs); per-station failure emails;
  per-station schedules and concurrency trivially.
- Cons: N schedule registrations, each subject to delay/drop/new-schedule flakiness
  and each needing its own recovery (finding 8); N files or N wrapper jobs buying
  nothing the matrix lacks at N = 2–5 with one shared deadline; deadline monitoring
  fragments into N checks. Earns its keep only when stations diverge in schedule,
  trigger type, or permissions.

## Implications for newsdesk (recommendation)

- Keep one scheduled workflow, one cron. Convert the `morning` job to a matrix:
  `strategy: { fail-fast: false, matrix: { station: [<ids>] } }`, pass
  `NEWSDESK_STATION: ${{ matrix.station }}` (the CLI resolves per-station params and
  token path segments from the station id), keep the seed step and every existing env
  mapping.
- Do not set `continue-on-error` — with the failure email as the only alerting
  channel, a run that "passes" despite a failed station is a silent miss.
  `fail-fast: false` alone is the isolation lever (finding 2).
- Keep the workflow-level `concurrency: morning` with `cancel-in-progress: false`
  unchanged: legs of one run don't contend, overlapping runs still queue, and the
  idempotency guard no-ops the loser. Job-level `morning-${{ matrix.station }}` groups
  are available later if a recovery dispatch should rebuild one station while another
  run holds the group (finding 3) — unnecessary at N = 2–5.
- Keep `timeout-minutes: 45`; it becomes a per-station budget, removing the loop's
  scaling ceiling (finding 4).
- Schedule posture inherits `gha-schedule-reliability.md` verbatim (off-hour cron,
  two-line recovery, failure email): the matrix neither helps nor hurts trigger delay,
  and it keeps publish time flat as stations are added — exactly what the 83-minute
  slack needs (finding 8).
- Revisit per-station workflow files only if stations diverge in schedule or trigger;
  the loop loses on every constraint it touches once N ≥ 2 (deadline math, timeout
  budget, isolation, visibility).
