# The morning briefing on GitHub Actions

The cloud half of the morning-audio pipeline (morning-audio-impl ticket 07).
One scheduled workflow runs the whole local pipeline — `newsdesk morning`:
collect → digest (with verdicts) → script → tts → publish → notify — with the
`cloudflare-pages` publisher, unattended. The workflow first seeds the fresh
runner database from the committed `seed/newsdesk-seed.json` (sources +
watchlist terms; re-export locally after changes). The notify registry ships
empty, so notify is a logged no-op until a notifier bolts on.

**Workflow:** `.github/workflows/morning.yml` · **Config contract tests:**
`tests/test_actions_workflow.py`

## Schedule and deadline

- Single cron `7 23 * * *` UTC = **07:07 HKT daily**. Deadline is
  publish-by-08:30 HKT; the 83 minutes of slack absorb median Actions runner
  delays plus the ~10-minute pipeline (evidence:
  `docs/research/gha-schedule-reliability.md`).
- Job timeout 45 min; overlapping runs queue (`concurrency: morning`); a
  re-run for an already-published date exits early via the idempotency guard.
- `workflow_dispatch` inputs:
  - *(empty)* — run for today; the normal test path.
  - `date` (`YYYY-MM-DD`, morning tz) — backfill a past episode
    (`newsdesk morning --date`).
  - `fail: true` — deliberate-failure probe: the job exits 1 before any work,
    so you can verify the failure-email channel on demand.

## One-time setup

Sources and watchlist terms ride into CI via the committed seed file
(`seed/newsdesk-seed.json`, imported by the workflow before
`newsdesk morning`). After changing sources or terms locally, re-run
`newsdesk seed export` and commit the diff — that's the whole sync.

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
| `NEWSDESK_FEED_TOKEN` | **Secret** | 64-hex | the feed's only auth (URL path segment) |
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

~10 min per run, once a day ⇒ **~300–400 min/month**, well inside the 2,000
free minutes of a private repo. pip is cached; wrangler is installed per run.

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
