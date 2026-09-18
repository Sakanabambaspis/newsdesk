# 07 — GitHub Actions schedule + secrets + two-phone rehearsal

**What to build:** The pipeline goes live on the schedule the user accepted
(wayfinder tickets 01/06): a workflow with a single cron, `7 23 * * *` UTC =
07:07 HKT daily, plus `workflow_dispatch` for testing and backfilling, that
runs `newsdesk morning` with the cloudflare publisher on ubuntu-latest.
Deadline is publish-by-08:30 HKT; the 83-minute slack absorbs median runner
delays plus the ~10-minute pipeline. Credentials per ticket 09: Actions
secret `NEWSDESK_LLM_API_KEY` with plain vars `NEWSDESK_LLM_BASE_URL` /
`NEWSDESK_LLM_MODEL`, plus `NEWSDESK_CLOUDFLARE_API_TOKEN` and
`NEWSDESK_FEED_TOKEN` — documented in one place; a missing LLM key must
still produce a published extractive episode (NullAdapter path, no extra
work). Failure visibility is GitHub's automatic workflow-failure emails —
no retry fire, no dead-man's switch, by decision.

The ticket closes with the full rehearsal that graduates the map's last fog:
subscribe both phones (iPhone — Apple Podcasts add-by-URL; Android —
AntennaPod with refresh reconfigured to ≤1h, or Pocket Casts with
notifications enabled once) and confirm a real morning lands before wake-up.

**Blocked by:** 05 — `newsdesk morning` orchestration;
06 — cloudflare-pages publisher

**Status:** needs-human (workflow + CLI backfill + docs + tests landed; the
live dispatch, failure-probe email, and two-phone rehearsal are the human
remainder — scripted in `docs/morning-actions.md`)

- [x] Workflow file: cron `7 23 * * *` UTC + manual dispatch; installs the
      package with the audio extra; runs `newsdesk morning` unattended.
      (`.github/workflows/morning.yml`; the extra is named `tts` in
      pyproject — that is the audio extra. Dispatch inputs: `date` backfill
      via the new `newsdesk morning --date`, and a `fail: true`
      deliberate-failure probe. Contract pinned by
      `tests/test_actions_workflow.py`.)
- [ ] Secrets/vars set per ticket 09 and documented; no credential value in
      any log (verified in a run's output). *(Documented in one place —
      `docs/morning-actions.md`; "set" and the no-leak log check are live
      steps for the human.)*
- [ ] A manual dispatch run publishes an episode to the real feed
      end-to-end.
- [ ] Deliberate-failure run produces the expected GitHub failure email —
      the error channel works. *(Probe built into the workflow: dispatch
      with `fail: true`.)*
- [ ] Scheduled-run cost within free quota (~300 min/month observed vs
      2,000 free).
- [ ] Rehearsal complete: both phones subscribed, auto-download/notification
      settings per the ticket-05 answers, and an episode listened to on each.

## Comments

Implemented 2026-09-18 (agent). 242 tests pass (10 new: 8 workflow-contract
in `tests/test_actions_workflow.py` + 2 CLI backfill in
`test_morning_orchestrator.py`).

- **`.github/workflows/morning.yml`** — single cron `7 23 * * *` UTC
  (07:07 HKT), `workflow_dispatch` with `date` (backfill) and `fail`
  (failure-probe) inputs, `permissions: contents: read`, job concurrency
  group `morning` (queued — the idempotency guard no-ops the loser), 45-min
  timeout, Python 3.13, `pip install .[tts]`, wrangler via npm, then
  `newsdesk morning --json` with dispatch inputs reaching the shell only via
  `MORNING_*` env (injection-safe). Credentials wired exactly per wayfinder
  ticket 09: secrets `NEWSDESK_FEED_TOKEN` /
  `NEWSDESK_CLOUDFLARE_API_TOKEN` / `NEWSDESK_LLM_API_KEY`, plain vars the
  rest. The LLM secret appears only as an env value — no gating, no
  branching — so a missing key still publishes the extractive episode
  (NullAdapter path; exercised by the whole keyless orchestrator suite).
- **`newsdesk morning --date YYYY-MM-DD`** — CLI backfill support the
  dispatch input needed (validated ISO date, normalized so `2026-9-1`
  reuses the `2026-09-01` episode key, passed to `run_morning`).
- **Code-review fix (§18):** the CLI report becomes the CI run log, and the
  report carried token-bearing URLs — the feed's only auth. `newsdesk
  morning` now masks the feed token (`***`) in its printed output, both
  human and `--json`; pinned by the CLI tests.
- **`docs/morning-actions.md`** — the one place: schedule/deadline, the
  secret/var table, failure visibility (GitHub failure email + the `fail`
  probe), cost math (~300–400 min/month vs 2,000 free), the no-leak log
  check, and the step-by-step two-phone rehearsal (Apple Podcasts
  add-by-URL; AntennaPod refresh ≤1h / Pocket Casts notifications).
- **For the human:** follow `docs/morning-actions.md` top to bottom — setup,
  live dispatch, both phones, failure probe, one real scheduled run, log
  scan — then tick the boxes above and set `Status: done`. The live dispatch
  also serves as ticket 06's live-deploy verification.
