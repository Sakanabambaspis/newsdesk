# CI topology for multi-station runs

Type: research
Status: resolved
Blocked by:

## Question

How should one scheduled GitHub Actions run fan out to N station runs — a
single job looping stations, a matrix strategy, or per-station workflow files
— given the documented schedule-delay findings
(`docs/research/gha-schedule-reliability.md`), the publish-by-deadline profile
of `docs/morning-actions.md`, the shared `morning` concurrency group, failure
isolation between stations, per-station secrets/vars, and the 45-minute
timeout budget?

Method: `/research` against primary sources (GitHub Actions docs) + the repo's
runbook; deliver findings to `docs/research/gha-station-topology.md` with a
recommendation. Resolution recorded below; the recommendation binds ticket 12.

## Answer

One scheduled workflow stays; the `morning` job becomes a matrix over station ids
(`strategy: { fail-fast: false, matrix: { station: [...] } }`,
`NEWSDESK_STATION: ${{ matrix.station }}`). `fail-fast: false` is the failure
isolation lever — the matrix default `true` cancels sibling stations when one fails —
and `continue-on-error` must be omitted because it would let the run "pass" and
suppress the failure email, the repo's only alerting. Keep the workflow-level
`morning` concurrency group and `timeout-minutes: 45` (now a per-station budget) as
is. Decisive trade-off: wall time under a delayed cron — a sequential loop hits the
45-minute job timeout around N≈5 and eats the 83-minute deadline slack, while matrix
wall time is ~one station regardless of N. Per-station workflow files are deferred
until stations need different schedules or triggers.

Evidence and doc URLs: `docs/research/gha-station-topology.md`.
