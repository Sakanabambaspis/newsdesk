# 05 — `newsdesk morning` orchestration

**What to build:** One command does the whole morning: collect → digest (with
verdicts) → script → tts → publish → notify → log. This is the command the
GitHub Action will invoke (wayfinder ticket 09). The notify registry ships
**empty** — zero registered implementations is a defined no-op, per the
delivery-route decision (ticket 05) — so Telegram/ntfy can bolt on in about
an hour later. The run keeps the cheap idempotency guard of ticket 06: if
today's episode is already published, exit 0 before doing work, so manual
re-runs can't double-publish and a future retry fire needs no redesign.
Engines/publisher are selected via env (`NEWSDESK_*`, consistent with the
existing Settings discipline); log entries record provider and outcome per
stage and never key material (DESIGN.md §18 extended to the pipeline).

Demoable locally end-to-end: one command produces the published episode in
the local-dir publisher's output; a second run exits idempotently.

**Blocked by:** 02 — Plugin registries + `llm-brief` script-writer + script sidecar;
03 — edge-tts plugin; 04 — Podcast feed generator + local-dir publisher

**Status:** ready-for-agent

- [ ] A single command runs all six stages in order against the local
      publisher and produces a subscribable feed + MP3 locally.
- [ ] Second run on the same date exits 0 without re-publishing (guard
      checks before doing work).
- [ ] Empty notify registry is a silent no-op, not an error.
- [ ] Immutable log carries one entry per stage (digest method, script,
      publish outcome), with no secrets or key material.
- [ ] Stage selection honors env (tts engine, publisher) with loud failure
      on an unknown name.
- [ ] Any stage failure exits non-zero with a safe, actionable message;
      partial artifacts don't get published as an episode.
- [ ] Tests cover the happy path, the idempotent re-run, the empty-notify
      no-op, and a mid-pipeline failure.
