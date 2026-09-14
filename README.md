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
pip install -e ".[dev]"

# everything is stored under $NEWSDESK_HOME (default ~/.newsdesk)
newsdesk add-source https://feeds.bbci.co.uk/news/world/rss.xml
newsdesk collect
newsdesk search "energy policy"
newsdesk item item_abc123...      # full canonical record as JSON
newsdesk log                      # immutable activity log
newsdesk serve                    # HTTP API + agent tools at http://127.0.0.1:8000/docs
```

Local files work too (`newsdesk add-source tests/fixtures/feeds/sample-energy.xml`),
which is how the offline test suite runs.

## What is implemented (M0)

- **Ingestion** — RSS/Atom with conditional GET, robots.txt compliance,
  per-host rate limiting, retries, raw payload snapshots.
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
  (summaries degrade gracefully).
- **Interfaces** — CLI (above) and HTTP API exposing the 9-tool agent protocol
  (`GET /tools` for discovery; see `docs/DESIGN.md` §10).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NEWSDESK_HOME` | `~/.newsdesk` | database + snapshots directory |
| `NEWSDESK_DB_URL` | SQLite under home | any SQLAlchemy URL (Postgres-ready) |
| `NEWSDESK_USER_AGENT` | `NewsdeskBot/0.1 ...` | identified user agent |
| `NEWSDESK_MIN_INTERVAL` | `5` | min seconds between requests to one host |
| `NEWSDESK_LLM_BASE_URL` / `NEWSDESK_LLM_API_KEY` / `NEWSDESK_LLM_MODEL` | unset | OpenAI-compatible LLM |

## Development

```bash
pip install -e ".[dev]"
pytest                # fully offline: fixture feeds + mocked transports
```

Layout: `newsdesk/ingest` (fetchers) · `newsdesk/pipeline` (normalize/dedupe/run)
· `newsdesk/storage` (repos, FTS, log) · `newsdesk/llm` (adapters) ·
`newsdesk/api` + `newsdesk/agents` (HTTP + protocol) · `newsdesk/cli`.

## Roadmap

M1 scheduling + HTML extraction → M2 clustering + Postgres → M3 LLM summaries →
M4 digests/alerts/feedback → M5 multimedia → M6 MCP + harness adapters.
Details and risk register in [docs/DESIGN.md](docs/DESIGN.md).
