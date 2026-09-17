# Map: Cloud morning-audio pipeline

Status: **complete** — all tickets resolved 2026-09-18; proceed to the
implementation hand-off (fog → `/to-tickets`).

## Destination

A decision-complete architecture for the cloud morning-audio pipeline — schedule,
hosting, notification route, spoken-script contract, and plugin seams all locked
with cited research — with fog graduated into tickets, ready to hand off to
`/to-tickets` for implementation. The email-digest audio rides the same pipeline
and is sketched (not built) as the follow-on.

## Notes

- **Domain:** newsdesk — local-first Python/SQLite news agent, provenance-first
  (`docs/DESIGN.md`). LLM adapter layer already exists (BaseLLMAdapter /
  NullAdapter / keyless extractive fallback). One command (`newsdesk collect`,
  `newsdesk digest-daily`) does the local work today.
- **Binding prior decisions:**
  - Runner = GitHub Actions (user decision, 2026-09-16).
  - **Everything a plugin** — TTS, publish, notify, script-writer are swappable
    registries, like the deepseek harness. This is the user's explicit,
    standing requirement.
  - ADR 0001 (`docs/adr/0001-digest-as-selector-for-the-morning-briefing.md`):
    the deterministic daily digest is the selector; the LLM only renders prose
    from a curated `MaterialPack`, with a signal/hype verdict pass. No opaque
    filtering (§1.2 posture holds in the cloud too).
  - Readiness by ~9am HKT, not 8:50 sharp (user accepted buffered schedule,
    2026-09-18).
  - Free-only budget (user, 2026-09-18). iPhone **and** Android must both work
    (user, 2026-09-18).
  - Credentials never in DB/log (DESIGN.md §18) — adapted for CI as Actions
    secrets.
- **Skills to consult:** research findings land in `docs/research/*.md`;
  `ousterhout-software-design` + `cordis-composability` when deciding the
  plugin seams (ticket 09).
- **Tracker:** local markdown (`.scratch/morning-audio-cloud/`), per
  `docs/agents/issue-tracker.md`. No `gh` CLI on this machine.
- This map supersedes the drafted-but-never-written
  `docs/proposals/morning-audio-pipeline-20260916.md`.

## Decisions so far

- [GitHub Actions can deliver by ~9am HKT — buffered cron, no keep-alive needed](issues/01-gha-schedule-reliability.md) — delays 5–15 min median (drops happen); use an off-hour cron like `7 0 * * *` + idempotent second fire; the 60-day disable is public-repo-only, so our private repo is exempt; quota is a non-issue.
- [Telegram is the best single notification route; podcast feed + ntfy poke is the best combination](issues/03-notification-route.md) — Telegram `sendAudio` streams in-chat (2 taps, ~10 min setup); ntfy topic name is the password; AntennaPod's 12h poll needs reconfiguring.
- [edge-tts works headless in CI; LLM = key-in-secret with extractive fallback](issues/04-tts-llm-in-ci.md) — live smoke test passed; plugin needs pacing + retries (break-then-fix history); OpenAI TTS is the natural second plugin; GitHub Models retired.
- [Free hosting: Cloudflare Pages with a path token](issues/02-hosting-free-private-feed.md) — Pages-on-private-repo is paywalled and its 1GB cap would force pruning anyway; Netlify's credit plan kills daily deploys; feed URL is a permanent secret that must live in a path segment, not a query string.
- [Delivery route: Cloudflare feed only — podcast apps are the clients, poke deferred](issues/05-delivery-route.md) — "ready before wake-up" beats punctuality, so the notify leg ships as an empty seam; GitHub failure emails are the error channel; retention became a real question (ticket 10).
- [Schedule: single cron `7 23 * * *` UTC (07:07 HKT), publish by 08:30](issues/06-schedule-policy.md) — no retry fire, no catch-up window, no dead-man's switch; GitHub failure emails only; idempotent run allows manual re-runs and a painless future retry.
- [Privacy: one 128-bit path token is the only auth; anonymous generic feed metadata](issues/07-privacy-token-model.md) — rotation is break-glass (re-subscribe both phones); per-device feeds are the documented escalation; synced podcast apps share the URL with their clouds.
- [Script: ~5 min all-English, 3 headlines + one deep dive, never padded](issues/08-script-contract.md) — deep dive = top-scoring signal item unpacked problem→approach→why→limits; section→item sidecar goes to the log, not the public feed; short days run short.
- [Retention: keep everything forever — publish only appends](issues/10-retention-and-pruning.md) — no host cap bites at ~1.8GB/yr, so v1 has no prune code; feed lists all episodes; a prune step is a later add-on.
- [Plugin seams: script-writer / tts / publish / notify behind four registries; existing OpenAI-compatible key](issues/09-plugin-seams-and-credentials.md) — one `newsdesk morning` command orchestrates; notify ships empty; LLM key in Actions secret with automatic extractive fallback; CF token + feed token secrets added with publish.

## Not yet specified

- **Email-digest audio** — selection/importance rules over the user's mailbox,
  privacy posture for processing personal mail in CI, which morning-pipeline
  plugins it reuses. Graduates once delivery route + seams are locked.
- **Phone-side setup checklist + full rehearsal** — subscribing both devices
  (Apple Podcasts / AntennaPod-or-Pocket-Casts), notification permissions, one
  end-to-end dry run of the workflow. Graduates when implementation tickets
  exist.
- **Implementation breakdown** — `newsdesk morning` command, workflow YAML,
  plugin packages. This is the hand-off to `/to-tickets` when the map
  completes.

## Out of scope

- **Schedule defenses (retry fire, catch-up window, dead-man's switch)** —
  declined 2026-09-18 until missed mornings "start to get annoying"; the
  idempotent run means any of them can be added later without redesign.
- **Routine notification poke (Telegram/ntfy)** — deferred 2026-09-18:
  "ready before wake-up" makes the poke unnecessary; the notify seam ships
  empty and can be filled in ~an hour if a missed morning proves the need.
- **Native iOS/Android app** — Apple's $99/yr developer fee violates the
  free-only constraint, and the tap-to-listen candidates already cover the UX.
  Revisit only if the delivery-route ticket fails every candidate.
- **Any paid hosting/plan** — budget decision, 2026-09-18.
- **8:50-sharp delivery** — user accepted by-~9am readiness with a buffered
  schedule; external triggers and self-hosted runners are not pursued.
- **New scraping postures** — the cloud pipeline runs the existing polite
  fetchers under DESIGN.md §1.2; loosening that is a separate effort.
