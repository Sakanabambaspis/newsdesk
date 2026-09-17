# Morning audio pipeline — design document

**Date:** 2026-09-16 · **Status:** design only; no code written. Implementation starts only on explicit green light.
**Anchor:** `docs/DESIGN.md` v0.1 (§4 stage contracts, §8 untrusted-data framing, §11 copyright posture, §18 account env contract) and the shipped M0/M0.5 scaffold. Glossary: `CONTEXT.md` (**Episode**, **Signal**, **Hype**, **Hot topic**).

Decisions recorded as made (original three plus the 2026-09-16 design review):

1. **A1 — trigger:** GitHub Actions cron (`0 23 * * *` UTC = 08:00 Beijing), `workflow_dispatch` for manual runs, self-hosted runner recorded as the future option.
2. **A2 — script + TTS:** LLM-rendered spoken script, edge-tts first plugin. Refined in review: the daily digest is the **selector**; a new context pack feeds the writer; language is **user-selected** (translation at script time); Signal/Hype verdicts filter hype from the script.
3. **A3 — delivery:** RSS 2.0 podcast feed (`outbox/feed.xml`) pushed to a `feed` branch; subscribed once in a podcast app, episodes arrive each morning.
4. **Scope (review):** build the script-writing and TTS modules first as isolated pieces; Actions/publish move after.

---

## 1. The one-command core

`newsdesk morning` chains five steps. Two exist; three are new:

| # | Step | Status | Input → Output |
|---|---|---|---|
| 1 | `collect` | exists (`pipeline/runner.py`) | sources → items in DB |
| 2 | `digest-daily` | exists (`pipeline/digest.py`) | items → digest dict |
| 3 | `script-digest` | **new** | digest dict + selected item texts → spoken-word script |
| 4 | `tts` | **new** | script → mp3 |
| 5 | `publish` | **new** | mp3 + metadata → outbox, podcast feed, push |

Each step is a separate CLI verb *and* a composable function; `morning` is just the chain. Failure isolation follows §4's contract: **a failed step never loses the briefing.**

- `collect` and `digest-daily` already isolate per-source/per-item (§4). `script-digest` failure falls back to the extractive renderer (§4 below) — a briefing script always exists. `tts` failure leaves the script in the outbox so the text briefing still ships. `publish` failure leaves the mp3 + feed locally; the next run republishes.
- Every step appends `morning_step_started` / `morning_step_finished` / `morning_step_failed` to the immutable log with the step name, provider, and duration — never secrets.
- `newsdesk morning --from digest.json` allows re-running audio/publish from an existing digest, so a TTS or publish failure never forces a re-collect.

---

## 2. Plugin registry as the spine

Every swappable stage uses the pattern already proven three times in this codebase: `ingest.base.register` fetcher kinds, `accounts.PROVIDERS`, LLM adapters behind `BaseLLMAdapter`. Same shape, three more registries:

- **`TTS_PROVIDERS`** — `@register_tts("edge" | "openai" | "piper")`, selected by `NEWSDESK_TTS_PROVIDER`. Each plugin implements `synthesize(script, settings, out_path) -> TtsResult` (voice segments, durations, mp3 path). Swapping TTS later = one decorated class in one file, zero core edits.
- **`PUBLISH_TARGETS`** — `@register_publish("gh-pages" | "rclone:<remote>" | "artifact")`, selected by `NEWSDESK_PUBLISH_TARGET`. Implements `publish(outbox, settings) -> PublishResult` (URLs, status). The `rclone:` prefix parses a remote name; the plugin does not parse it.
- **`SCRIPT_RENDERERS`** — `@register_renderer("podcast" | "summary")`, selected by `NEWSDESK_SCRIPT_RENDERER`. `podcast` is the spoken-word renderer (§4); `summary` is the flat extractive read of the digest (also the no-LLM fallback).

Registries live next to their stage (`newsdesk/audio/` for tts + script, `newsdesk/publishing/` for targets). Unknown provider names fail at startup with the list of registered names — same behavior as an unknown fetcher kind.

**Triggers are external by design.** Actions cron, launchd, or a human typing `newsdesk morning` all run the identical command; the pipeline never knows who called it. The workflow file (§6) is configuration, not code the pipeline contains.

---

## 3. A1 — Scheduling on GitHub Actions

**Primary trigger:** Actions `cron: '0 23 * * *'` UTC = 08:00 Beijing daily. `workflow_dispatch` for manual runs. No scheduler code ships in the pipeline.

**Bilibili caveat (recorded, accepted):** GitHub-hosted runners are Azure IPs; Bilibili rejects them (observed ~352 s of failures before giving up). The pipeline degrades gracefully per §1 — the briefing ships without Bilibili items, and the per-source stats in the job log show exactly what was skipped. Self-hosted runner is the recorded future option if Bilibili coverage matters; it requires zero pipeline changes, only a runner label on the workflow.

**State persistence:** `$NEWSDESK_HOME` (SQLite DB, snapshots, accounts metadata) survives via `actions/cache` keyed on the runner OS + a week bucket. Daily runs keep the cache warm; a weekly DB artifact upload is the belt-and-braces backup so an evicted cache costs at most a week of history (§4's "every step tolerates an empty home" means a cold run rebuilds from scratch and still ships a briefing). Conditional GET makes re-collection after eviction cheap.

**Secrets map 1:1 onto the existing env contract — nothing new:**

| Workflow secret | Existing variable |
|---|---|
| `NEWSDESK_LLM_API_KEY` / `_BASE_URL` / `_MODEL` | §8 LLM adapter env (optional; extractive fallback without it) |
| `NEWSDESK_TTS_PROVIDER` / voice/rate settings | new but plain env, no secret for edge-tts |
| `NEWSDESK_EMAIL_*`, `NEWSDESK_TWITTER_ACCESS_TOKEN`, `NEWSDESK_YOUTUBE_*` | §18 account envs, unchanged |

The deploy key for the `feed` branch is the only genuinely new credential, and it is write-scoped to that branch via a fine-grained deploy key.

---

## 4. A2 — Script + TTS

### 4.1 Purpose of the briefing (the domain contract)

The episode keeps the listener current on **newest AI research in the selected
areas** (the watchlist's research-area terms) and on **which topics are hot**.
It is not a general news read-out. The vocabulary is normative (see
`CONTEXT.md`):

- **Signal** — a novel engineering solution to an open problem, or a measured
  result that changes what's known in a selected area.
- **Hype** — what a person or company said, promised, launched, or raised
  (CEO statements, personality news, funding rounds, startup launch posts).
  Never Signal; excluded from the script; **kept in the digest dict** so every
  exclusion is auditable and overridable.
- **Hot topic** — a selected area with unusually many items from independent
  publishers in the current window. The script opens with hot topics, not
  chronological items; week-over-week trend vs. prior digests is surfaced in
  the digest header.

### 4.2 Architecture: selection and prose are separate machines

The daily digest (`build_daily_digest`, already shipped) is the **selector** —
deterministic, free, auditable (ranking scores, matched terms, logged via
`daily_digest_built`). The LLM's only job is prose. Design rejected: feeding
raw scraped materials straight to the writer (unbounded context, selection
becomes an unauditable model judgment, ~100× the prompt-injection surface).

**Step 1 — the selector (exists, unchanged):** 24h window, watchlist-term
scoring, rank, cap at `MAX_LLM_ITEMS`, theme grouping. Watchlist terms *are*
the research areas; theme structure is the "selected areas" view.

**Step 2 — new: the context pack.** `build_context(digest, item_repo,
budget) -> MaterialPack` — a small pure function that hydrates the digest's
selected item IDs with full text from storage and fits them under a character
budget (~20–30k chars) by mechanical, ordered rules: theme-grouped in ranked
order, per-item body truncation, lowest-ranked dropped last. Deterministic,
snapshot-able, unit-testable with no LLM. This is the "clear context window"
as an artifact — the only place the truncation policy lives.

**Step 3 — new: Signal/Hype verdicts (B1).** The digest's LLM pass is widened:
each candidate item gets a stored verdict — `technical | hype | tangential` —
with a one-line reason, kept beside its item card. Hype items are excluded
from the script but remain in the digest dict (no silent filtering — §1.2
posture). Cheap exclusion keywords on top still compose (knock out the
obvious; the verdict catches the subtle). The extractive fallback path skips
verdicts rather than failing. Source mix is the first filter: arXiv-heavy
sources (`ingest/arxiv.py`) pre-dilute hype so verdicts confirm rather than
decide.

**Step 4 — the script writer (one LLM call, one job).** Input: MaterialPack
(hype items already excluded), target language. The system prompt quotes the
glossary and inherits §8's framing verbatim: material blocks are
`<source>`-tagged **untrusted data**; cover the pack's themes; attribute every
claim to a publisher by name ("according to Ars Technica…") — item IDs are
never speakable text; never invent what a source didn't say; write in
`NEWSDESK_BRIEFING_LANGUAGE`; 800–1,100 words. Output parsing uses the same
strict JSON-or-contained-error contract as `llm/base.py`.

**Fallback is structural:** no key, LLM error, or unparseable output → the
writer renders the pack's themes and top titles as a plain read-out script.
Same `Script` type, same interface, worse prose. `write_script` never raises
for these conditions (errors defined out of existence); aggregation of "what
failed" lives at the `morning` chaining level.

### 4.3 The Script type and TTS

- **`Script` is a small typed object, not a string** — language, title, prose
  segments, and the attribution list. Storing it (next to the future mp3)
  makes the audit trail automatic: what was said is always diffable against
  what the sources said.
- **TTS interface:** `render_audio(script, settings, out_path) -> AudioResult`.
  No `voice` parameter — complexity pulled downward. Source names and quoted
  English terms inside translated text still need mixed-script handling: a
  multilingual voice (e.g. `zh-CN-XiaoxiaoMultilingual`) or per-segment
  language tags the writer emits; decided inside the TTS module.
- **edge-tts** remains the first `TTS_PROVIDERS` plugin (free, no key);
  `openai` / `piper` stay future registry entries (§2).

---

## 5. A3 — Delivery

`newsdesk publish` writes into `$NEWSDESK_HOME/outbox/`:

- `feed.xml` — RSS 2.0 podcast feed with `<enclosure>` per episode and the `itunes:` tags podcast apps require (title, duration, image, explicit=false).
- `episodes/<date>-<hash>.mp3` — daily mp3s addressed under an **unguessable-token URL** (security by URL obscurity is acceptable here because the content is our own spoken words, but the token still keeps the feed out of directory listings and search).
  **Open decision (flagged in design review):** the original "token generated once at first publish" breaks under `actions/cache` eviction — a new token would silently kill the podcast subscription. Recommended resolution, pending confirmation: the token is a repo secret (`NEWSDESK_FEED_TOKEN`), stable regardless of cache state. Alternatives (HMAC derived from an existing secret — couples key rotation to the feed URL; dropping the token entirely) were rejected or left unchosen.
- `<date>-script.md` — the stored script (§4), next to its audio.

**`gh-pages` publish target** pushes the outbox to a `feed` branch (GitHub Pages can serve any branch). Subscribe once in Overcast/Pocket Casts; each morning's episode auto-downloads. `rclone:<remote>` and `artifact` targets are registry entries for non-GitHub setups; the artifact target is also how the Actions run attaches the mp3 for immediate inspection without a feed.

**Copyright posture (per §11):** the briefing is our own words — LLM-rendered or extractive summaries, with publisher attributions and links in the show notes. It never reads out articles. Feed show notes carry item URLs so listeners can go to the source.

---

## 6. A4 — The workflow file (sketch)

```yaml
# .github/workflows/morning.yml
name: morning
on:
  schedule: [{cron: '0 23 * * *'}]   # 08:00 Beijing
  workflow_dispatch: {}

concurrency:
  group: morning                      # a late run won't overlap the next
  cancel-in-progress: false

jobs:
  morning:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --extra all
      - name: Restore state
        uses: actions/cache@v4
        with:
          path: ~/newsdesk-home
          key: newsdesk-home-${{ runner.os }}
          restore-keys: newsdesk-home-    # tolerate eviction: partial match
      - name: Collect + morning
        env:
          NEWSDESK_HOME: ~/newsdesk-home
          NEWSDESK_LLM_API_KEY: ${{ secrets.NEWSDESK_LLM_API_KEY }}
          # ...existing §8/§18 env contract, 1:1 from secrets
        run: |
          uv run newsdesk collect || true   # per-source isolation inside;
                                            # never lose the briefing to a bad source
          uv run newsdesk morning
      - name: Publish feed branch
        run: uv run newsdesk publish --target gh-pages
        env: { FEED_DEPLOY_KEY: ${{ secrets.FEED_DEPLOY_KEY }} }
      - uses: actions/upload-artifact@v4
        with: { name: episode, path: ~/newsdesk-home/outbox/episodes/ }
```

Notes: cache eviction is tolerated everywhere (cold run rebuilds, briefing still ships); the weekly DB-artifact backup step is added to the same workflow on a `schedule` day-of-week condition; cost is **$0** (public repo minutes + edge-tts + no paid APIs).

---

## 7. Build order and done checks

| # | Step | Size | Done check |
|---|---|---|---|
| 1 | Context pack + `SCRIPT_RENDERERS` (podcast writer with Signal/Hype verdicts + extractive fallback) | S→M | unit test: `build_context` on a fixture digest fits the budget with deterministic truncation order and no LLM; `newsdesk script-digest digest.json` produces 800–1,100 words in `NEWSDESK_BRIEFING_LANGUAGE` with publisher names, zero item IDs, and hype items absent; no-key path returns a valid `Script` |
| 2 | `TTS_PROVIDERS` + edge-tts plugin | S | `newsdesk tts script.md` produces a non-empty mp3 (fixture: silent/dummy provider in tests; edge-tts smoke-tested manually once) — signature consumes the typed `Script`, mixed-script text handled inside the module |
| 3 | `publish` + `PUBLISH_TARGETS` + feed.xml writer | M | `newsdesk publish` on a fixture outbox yields `feed.xml` that passes a podcast-feed validator unit test (enclosure URL, itunes tags, content-type/length); gh-pages target push exercised against a scratch repo |
| 4 | `newsdesk morning` chaining | S | integration test: with every stage mocked, a scripted digest failure still emits an extractive script and the run logs all five `morning_step_*` entries; `--from digest.json` re-runs steps 3–5 only |
| 5 | Actions workflow file | M | a manual `workflow_dispatch` run on the real repo completes green with an episode artifact and an updated feed branch |
| 6 | (optional) self-hosted runner for Bilibili | S | documented runner setup in the workflow README; Bilibili source succeeds on that runner |

Steps 1–4 are pure code with offline tests; step 5 is the only one requiring repo secrets. **Everything above is design-only until the maintainer green-lights implementation.**
