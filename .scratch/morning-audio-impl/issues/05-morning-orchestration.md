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

**Status:** done

- [x] A single command runs all six stages in order against the local
      publisher and produces a subscribable feed + MP3 locally.
- [x] Second run on the same date exits 0 without re-publishing (guard
      checks before doing work).
- [x] Empty notify registry is a silent no-op, not an error.
- [x] Immutable log carries one entry per stage (digest method, script,
      publish outcome), with no secrets or key material.
- [x] Stage selection honors env (tts engine, publisher) with loud failure
      on an unknown name.
- [x] Any stage failure exits non-zero with a safe, actionable message;
      partial artifacts don't get published as an episode.
- [x] Tests cover the happy path, the idempotent re-run, the empty-notify
      no-op, and a mid-pipeline failure.

## Comments

Implemented 2026-09-18 (frontier worker). 216 tests pass (7 new). Live demo
verified twice against a scratch home: first run published (quiet-day script
→ real edge-tts render → local feed, exit 0), second run "already published
— nothing to do", exit 0; feedparser validates the feed.

- **`morning/orchestrator.py`** — `run_morning(session, settings)`: guard
  first (publisher's `already_published`, new contract attribute on the
  local-dir plugin — checks the manifest before any work), then collect →
  digest (24h window, no catch-up per schedule ticket 06) → script → tts →
  publish → notify. `stage_script` is shared with the `newsdesk script`
  command (single `morning_brief_built` log site).
- **Selection:** `NEWSDESK_TTS_ENGINE` (default edge-tts) and
  `NEWSDESK_PUBLISHER` (default local-dir; the CI workflow will set
  cloudflare-pages explicitly in ticket 07). Selection failures are
  attributed to a "selection" stage and logged like any other failure.
- **Failure semantics:** every stage failure (including selection) is logged
  as `morning_run_failed` {date, stage, safe error} and re-raised as
  `MorningError` → CLI exit 1; nothing after the failed stage runs, so no
  partial episode is ever published.
- **Log discipline:** per-stage entries
  (`daily_digest_built`/`morning_brief_built`/`morning_audio_rendered`/
  `morning_episode_published`/`morning_notify`/`morning_run_finished`) carry
  outcomes only — a test pins that the feed token appears in no log detail
  (token-bearing URLs are returned to the caller, printed for the human,
  never logged).
- **Notify:** iterates registered notifiers; the empty registry logs
  `morning_notify {notifiers: [], outcome: "no-op"}` and moves on.
- **For ticket 06:** implement `publish` + `already_published` (remote check:
  fetch `<base>/<token>/feed.xml`, look for the date) on the cloudflare
  plugin and register as `cloudflare-pages`; the orchestrator needs nothing
  else. **For ticket 07:** workflow sets `NEWSDESK_PUBLISHER=cloudflare-pages`
  and the secrets; the command surface is done.

