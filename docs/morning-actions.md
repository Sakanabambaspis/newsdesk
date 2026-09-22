# The morning briefing on GitHub Actions

The cloud half of the morning-audio pipeline (morning-audio-impl ticket 07).
One scheduled workflow runs the whole local pipeline — `newsdesk morning`:
collect → digest (with verdicts) → script → tts → publish → notify — with the
`cloudflare-pages` publisher, unattended. The workflow first seeds the fresh
runner database from the committed `seed/newsdesk-seed.json` (sources,
watchlist terms, workflow history, and stations; re-export locally after
changes); `newsdesk morning` itself bootstraps the shipped `default-morning@1`
into the workflow catalog on first use. The notify registry ships
empty, so notify is a logged no-op until a notifier bolts on.

**Workflow:** `.github/workflows/morning.yml` · **Config contract tests:**
`tests/test_actions_workflow.py`

## Stations: one schedule, one matrix, N feeds (W4)

The `morning` job is a **matrix over station ids** (research:
`docs/research/gha-station-topology.md`). Each leg sets
`NEWSDESK_STATION: ${{ matrix.station }}`, so it runs that station's bound
workflow against its scoped watchlist and publishes its own feed under
`https://<project>.pages.dev/<token>/<path_segment>/feed.xml` (the seeded
default station `morning-briefing` has no path segment: it *is* the legacy
root feed, byte-for-byte — same URLs, same GUIDs, same identity).

- `strategy.fail-fast: false` isolates the legs: one station's failure
  never cancels its siblings. `continue-on-error` is deliberately absent —
  a failed leg must fail the run, or the failure email (the only alerting
  channel) goes silent.
- The 45-minute timeout and the workflow-level `concurrency: morning` are
  unchanged; the timeout is now a *per-station* budget and the matrix wall
  time stays ≈ one leg.
- Idempotency is per (station, date): a re-run (or the second cron line of
  the recovery pattern) no-ops the stations that already published and
  rebuilds only the missing ones.
- Because a Pages deployment is the complete site, each station leg fetches
  back and restages the **union of all stations' archives** before deploy —
  a deploy that cannot include every station's history is refused (the
  archive-intact posture, widened). The station list comes from the
  stations table (the leg seeds first).
- **Adding a station:** add it to the seed (`newsdesk seed export` after
  creating it — stations enter the DB via seed or agent tools), add its id
  to the matrix list in `morning.yml`, commit both. No new secrets: the
  feed token, base URL and Cloudflare credentials are shared; the only
  per-station env is `NEWSDESK_STATION`.

## The episode archive: the script lands in the repo (ticket 04)

The feed is anonymous by design and carries **audio only** — content via
subscription would mean transcribing audio. The exact content is the
pre-TTS script sidecar, but it lives under `NEWSDESK_HOME` on the runner
and evaporates with the job. So the run that publishes also archives the
episode into the repo: each leg runs `newsdesk morning --archive briefing`,
then the **Commit the episode archive back to main** step lands the bundle:

```
briefing/<station>/<date>/script.json   # the sidecar, byte-for-byte
briefing/<station>/<date>/meta.json     # which version owns the podcast
```

`meta.json` is the provenance record: the workflow `name@version` that ran,
the code commit (`GITHUB_SHA`), the digest/script/tts stage facts, the
episode GUID, and the episode's path on the host (`audio/<date>.mp3` —
token-free by construction; the token appears in exactly one place: the
feed). The private repo is not the published site, so the sidecar's
"never published" posture (morning-audio ticket 07) is untouched.

- The commit step pushes `HEAD:main` after a rebase — matrix legs race on
  push, but bundle paths are disjoint per station so the replay never
  conflicts. Three retries, then the leg fails like any other.
- This is why the job needs `permissions: contents: write` and a full
  checkout (`fetch-depth: 0` — a shallow clone cannot rebase).
- An already-published re-run is a fresh runner with no sidecar: both the
  archive and the commit no-op (green, by design). A same-day *backfill*
  (`--date`) archives too, because it rebuilds and re-publishes.
- Reading it: `briefing/<station>/LATEST`-style pointers don't exist —
  browse by date, or `git log episode/` for the daily commits.

## Schedule and deadline

- Single cron `7 23 * * *` UTC = **07:07 HKT daily**. Deadline is
  publish-by-08:30 HKT; the 83 minutes of slack absorb median Actions runner
  delays plus the ~10-minute pipeline (evidence:
  `docs/research/gha-schedule-reliability.md`). One cron drives the whole
  station matrix — per-station schedules would multiply the delay/drop
  surface for nothing at N = 2–5.
- Job timeout 45 min per station leg; overlapping runs queue
  (`concurrency: morning`); a re-run for an already-published (station,
  date) exits early via the idempotency guard and heals only the missing
  stations.
- `workflow_dispatch` inputs:
  - *(empty)* — run for today; the normal test path.
  - `date` (`YYYY-MM-DD`, morning tz) — backfill a past episode
    (`newsdesk morning --date`).
  - `fail: true` — deliberate-failure probe: the job exits 1 before any work,
    so you can verify the failure-email channel on demand.

## One-time setup

Sources and watchlist terms ride into CI via the committed seed file
(`seed/newsdesk-seed.json`, imported by the workflow before
`newsdesk morning`; seed format 2 also carries the workflow catalog's
full history and the stations). After changing sources, terms, workflows,
or stations locally, re-run `newsdesk seed export` and commit the diff —
that's the whole sync.

Cloudflare side (details in ticket 06's checklist): create a **Pages**
project ("Direct Upload", empty — e.g. `morning-briefing`), note the
**Account ID**, and create an API token with **Account → Cloudflare Pages →
Edit**. The placeholder deployment must include a `404.html`
(`pages-placeholder/` in the repo is ready to drag in): a site with a root
`index.html` and no `404.html` is treated as a single-page app, so every
unknown path returns the index page at HTTP 200 — the publisher's archive
guard reads that as a corrupt `episodes.json` and refuses to deploy.
Generate the feed token:

```bash
python3 -c "import secrets; print(secrets.token_hex(16))"
```

Then fill repo → **Settings → Secrets and variables → Actions** (ticket 09
split — the secret/var distinction is deliberate):

| Name | Kind | Example | What it is |
|---|---|---|---|
| `NEWSDESK_FEED_TOKEN` | **Secret** | 32-hex (`secrets.token_hex(16)`) | the feed's only auth (URL path segment) |
| `NEWSDESK_CLOUDFLARE_API_TOKEN` | **Secret** | — | Cloudflare Pages:Edit API token |
| `NEWSDESK_LLM_API_KEY` | **Secret** (optional) | — | OpenAI-compatible key; **omit ⇒ NullAdapter ⇒ extractive episode still publishes** |
| `NEWSDESK_FEED_BASE_URL` | Variable | `https://morning-briefing.pages.dev` | Pages project production URL |
| `NEWSDESK_CLOUDFLARE_ACCOUNT_ID` | Variable | 32-hex | Cloudflare account id (identifier, not a credential) |
| `NEWSDESK_CLOUDFLARE_PROJECT` | Variable | `morning-briefing` | Pages project name |
| `NEWSDESK_LLM_BASE_URL` | Variable | `https://…/v1` | OpenAI-compatible endpoint |
| `NEWSDESK_LLM_MODEL` | Variable | `z-ai/glm-5.2:free` | primary model (any OpenRouter `:free` id) |
| `NEWSDESK_LLM_FALLBACK_MODELS` | Variable | *(empty)* | optional comma-separated fallback chain, tried in order on failure |

The LLM adapter retries transient upstream blips (429/502/503, 5/15/45 s
backoff) per model, then walks `NEWSDESK_LLM_FALLBACK_MODELS` in order —
set it to stay on free capacity when one provider has a bad hour. The
digest report's `verdict_method` names the model outcome either way.

Credential discipline (DESIGN.md §18, extended to CI by wayfinder ticket 09):
secrets ride Actions secrets → workflow env only — never argv, never the
database, never the log. The code redacts both secret values from error
output, the workflow never echoes its env, and `newsdesk morning` masks the
feed token in its printed report (`***`-masked URLs), so the run log — the
JSON report is the run log — carries no credential material. The
token-bearing episode/feed URLs appear in exactly one place: the published
feed itself.

## Failure visibility

GitHub's automatic workflow-failure email is the whole alerting story — no
retry fire, no dead-man's switch (schedule-policy decision, wayfinder ticket
06). A failed day simply has no episode; recover with a `workflow_dispatch`
run (optionally with `date` to backfill).

## Cost

~10 min per station leg, once a day — the matrix repeats setup per leg, so
the bill scales roughly linearly with stations (**~300–400 min/month** for
the first station, each additional station ≈ +300–400), well inside the
2,000 free minutes of a private repo at N = 2–5. pip is cached; wrangler is
installed per run.

## No-leak check (run after each live dispatch)

Open the raw run log and search for the feed token and the API token values.
Expected: zero hits — the publish stage logs outcomes, never URLs (the URLs
embed the token) and never key material.

## Rehearsal — the human closure of ticket 07

1. Do the one-time setup, then **dispatch a manual run** (no inputs). Expect
   a green run and a live episode at
   `https://<project>.pages.dev/<token>/feed.xml` that parses in feedparser.
2. **iPhone:** Apple Podcasts → Library → ⋯ → *Follow a Show by URL* → paste
   the feed URL → follow the show → enable its notifications.
3. **Android:** AntennaPod → *Add podcast* → RSS address → paste URL; set
   *Automatic refresh* to ≤ 1 h and enable episode auto-download (or Pocket
   Casts: subscribe + per-podcast notifications on).
4. Dispatch a run with `fail: true` → confirm the **failure email** arrives.
5. Let one real 07:07 HKT scheduled run happen; confirm it lands before
   08:30 and **both phones notify**; play the episode on each.
6. Do the no-leak check on that run's log.

Then tick ticket 07's remaining boxes and set its `Status: done`.
