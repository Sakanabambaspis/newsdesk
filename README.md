# Newsdesk

Watchlist-driven news collection agent with provenance-first logging.
Every fetched item keeps its original URL, publisher, timestamps, content hash,
extraction method, and raw snapshot — so any claim can be traced back to what
the source actually served.

**Full design: [docs/DESIGN.md](docs/DESIGN.md)**

## Quickstart

Requires Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows (Git Bash: source .venv/Scripts/activate)
pip install -e ".[all]"           # core + yt-dlp + ffmpeg wheel + MCP SDK

# everything is stored under $NEWSDESK_HOME (default ~/.newsdesk)
newsdesk add-source https://feeds.bbci.co.uk/news/world/rss.xml
newsdesk add-source https://www.youtube.com/@TwoMinutePapers --kind youtube
newsdesk add-source https://space.bilibili.com/22697887 --kind video
newsdesk collect
newsdesk search "energy policy"
newsdesk item item_abc123...      # full canonical record as JSON
newsdesk digest item_abc123...    # watch a video: transcript + keyframes + summary
newsdesk digest-daily             # the daily briefing: what is worth following, and why
newsdesk digest-daily --deep 3    # ... with inline per-item summaries for the top 3
newsdesk log                      # immutable activity log
newsdesk serve                    # HTTP API + agent tools at http://127.0.0.1:8000/docs
newsdesk mcp                      # MCP server (stdio) for agent harnesses
```

Local files work too (`newsdesk add-source tests/fixtures/feeds/sample-energy.xml`),
which is how the offline test suite runs.

## Using it from your agent harness

Newsdesk is an MCP addon: ZCode, Claude Code, Codex, and any MCP client can
drive the same database. See **[docs/HARNESS.md](docs/HARNESS.md)** for
per-harness registration snippets.

## What is implemented (M0 + M0.5)

- **Ingestion** — RSS/Atom with conditional GET, robots.txt compliance,
  per-host rate limiting, retries, raw payload snapshots. arXiv sources use
  the official arXiv API (`arxiv://<category>`) with their documented
  etiquette — export.arxiv.org's robots.txt disallows the web paths, but the
  API is the published programmatic interface.
- **Canonical records** — every story stored in one stable JSON shape
  (id / source / timestamps / content / analysis / provenance).
- **Deduplication, tier 1** — same URL + same content = no-op; changed content
  = revision (kept in the log); same content elsewhere = syndication link,
  never dropped — so "how many *independent* publishers reported this?" stays
  answerable.
- **Search** — SQLite FTS5 full-text index over titles, text, publishers.
- **Immutable log** — append-only record of every collection, item created or
  revised, error, and skip.
- **LLM adapter layer** — OpenAI-compatible endpoint via
  `NEWSDESK_LLM_BASE_URL` / `NEWSDESK_LLM_API_KEY`; runs without a key
  (summaries degrade to a labeled extractive fallback).
- **Video watching (M0.5)** — yt-dlp-backed `youtube` / `video` source kinds
  (channels, Bilibili spaces); caption-derived transcripts stored per item;
  `newsdesk digest <item>` runs transcript → keyframe extraction (bundled
  static ffmpeg) → vision description → grounded summary; audio
  transcription via any `/audio/transcriptions` endpoint when
  `NEWSDESK_TRANSCRIBE_AUDIO=1`.
- **Summaries** — `newsdesk summarize` / `POST /tools/summarize_item` /
  MCP `summarize_item`; evidence-linked JSON with item-ID citations.
- **Daily briefing** — `newsdesk digest-daily` / `GET /tools/daily_digest` /
  MCP `create_digest`: ranks the window's items by watchlist relevance,
  groups them into themes, and produces a grounded "what is worth following"
  narrative (one LLM call; extractive fallback without a key). Every section
  cites item IDs for drill-down; `--deep N` inlines per-item summaries.
- **Interfaces** — CLI (above), HTTP API exposing the tool protocol
  (`GET /tools`), and an MCP server (`newsdesk mcp` / `newsdesk-mcp`) for
  agent harnesses (see `docs/HARNESS.md`).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NEWSDESK_HOME` | `~/.newsdesk` | database + snapshots + media directory |
| `NEWSDESK_DB_URL` | SQLite under home | any SQLAlchemy URL (Postgres-ready) |
| `NEWSDESK_USER_AGENT` | `NewsdeskBot/0.1 ...` | identified user agent |
| `NEWSDESK_REQUEST_TIMEOUT` | `20` | HTTP fetch timeout in seconds (LLM calls have their own) |
| `NEWSDESK_MIN_INTERVAL` | `5` | min seconds between requests to one host |
| `NEWSDESK_LLM_BASE_URL` / `NEWSDESK_LLM_API_KEY` / `NEWSDESK_LLM_MODEL` | unset | OpenAI-compatible LLM (chat summaries, vision); `OPENAI_BASE_URL` / `OPENAI_API_KEY` are read as fallbacks |
| `NEWSDESK_LLM_AUDIO_MODEL` | `whisper-1` | `/audio/transcriptions` model |
| `NEWSDESK_VISION_MODEL` | = `NEWSDESK_LLM_MODEL` | vision-capable model for keyframes |
| `NEWSDESK_TRANSCRIBE_AUDIO` | `0` | transcribe caption-less audio (`1`/`true`/`yes`; costs bandwidth + tokens) |
| `NEWSDESK_KEYFRAME_INTERVAL` | `30` | seconds between extracted keyframes |
| `NEWSDESK_EMAIL_HOST/PORT/USER/PASSWORD` | unset | IMAP account (or `NEWSDESK_ACCOUNT__EMAIL__<REF>__*`) |
| `NEWSDESK_TWITTER_ACCESS_TOKEN` | unset | X API v2 OAuth 2.0 user token (`read` scope) |
| `NEWSDESK_YOUTUBE_API_KEY` / `NEWSDESK_YOUTUBE_CHANNEL_ID` | unset | subscription sync via Data API v3 |

## Linked accounts (email, X/Twitter, YouTube)

Read from your own mailbox and timelines under explicit, revocable consent.
Credentials come from env or the OS keyring (`pip install -e ".[accounts]"`)
— never the database. Grant capabilities, verify, collect:

```bash
newsdesk accounts grant email default --cap read_mail
newsdesk accounts test email default
newsdesk add-source "imap://imap.gmail.com/INBOX?account=default" --kind email
newsdesk collect

newsdesk accounts grant youtube-account default --cap read_subscriptions
newsdesk accounts sync-youtube        # registers your subscriptions as sources

newsdesk accounts list                # grants + credential presence (values never shown)
newsdesk accounts revoke email default
```

Mail is fetched read-only (`BODY.PEEK`, never marks messages seen), items use
stable `mid:` message-id URLs, and every authenticated action is logged under
an `account:<kind>/<ref>` actor. Details in `docs/DESIGN.md` §18.

## The morning briefing

`newsdesk morning` is the whole pipeline in one command: collect → daily
digest (with verdicts) → spoken script → TTS → publish → notify
(`--json` for a machine report, `--date YYYY-MM-DD` to backfill). The same
command runs unattended on GitHub Actions at 07:07 HKT daily and publishes a
private-token podcast feed to Cloudflare Pages. Setup, secrets, rehearsal:
**[docs/morning-actions.md](docs/morning-actions.md)**.

## Development

```bash
pip install -e ".[all,dev]"
pytest                # fully offline: fixture feeds + mocked transports + fake yt-dlp
```

Layout: `newsdesk/ingest` (fetchers incl. video, raw snapshots) ·
`newsdesk/pipeline` (normalize/run/summarize/digest) · `newsdesk/storage`
(repos, FTS, log) · `newsdesk/llm` (adapters) · `newsdesk/media`
(transcripts, vision) · `newsdesk/accounts` (email/X/YouTube account
providers) · `newsdesk/api` + `newsdesk/agents` + `newsdesk/mcp_server`
(HTTP + protocol + MCP) · `newsdesk/cli`.

## Roadmap

M1 scheduling + HTML extraction → M2 clustering + Postgres → M3 entity/topic
LLM wiring → M4 digests/alerts/feedback → M5 multimedia depth (OCR,
embeddings) → M6 further harness adapters → M7 OAuth refresh, JMAP, quota
caps on top of the shipped account layer.
Details and risk register in [docs/DESIGN.md](docs/DESIGN.md).
