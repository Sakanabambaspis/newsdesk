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

**Status:** ready-for-agent

- [ ] Workflow file: cron `7 23 * * *` UTC + manual dispatch; installs the
      package with the audio extra; runs `newsdesk morning` unattended.
- [ ] Secrets/vars set per ticket 09 and documented; no credential value in
      any log (verified in a run's output).
- [ ] A manual dispatch run publishes an episode to the real feed
      end-to-end.
- [ ] Deliberate-failure run produces the expected GitHub failure email —
      the error channel works.
- [ ] Scheduled-run cost within free quota (~300 min/month observed vs
      2,000 free).
- [ ] Rehearsal complete: both phones subscribed, auto-download/notification
      settings per the ticket-05 answers, and an episode listened to on each.
