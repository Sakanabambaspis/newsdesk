# Newsdesk — Watchlist-Driven News Collection Agent
## Session deliverable: design doc + runnable Python scaffold (local single-user)

Working name `newsdesk` (easily renamable). Project lives at the workspace root: `C:\Users\q\.zcode\workspace\default`.

---

## 1. `docs/DESIGN.md` — the full product/architecture plan

Structured as the canonical design reference, extending your brief:

- **Product definition & differentiation** — watchlist as the primary object; durable research log vs. daily digest.
- **Domain model** — Watchlist, Source, Item, Cluster, Digest, AlertRule, FeedbackEvent, LogEntry. Your canonical Item JSON is kept verbatim as the storage contract (id / source / timestamps / content / analysis / provenance).
- **Pipeline architecture** — the 10-stage flow (registry → fetchers → raw capture → parse → normalize → dedupe → score → summarize → log/index → outputs), with each stage's input, output, and failure semantics.
- **Ingestion adapters & rollout order** — RSS/Atom first; then sitemap/HTML, publisher APIs, YouTube/podcast metadata, transcription, email forwarding, forum webhooks, manual uploads.
- **Provenance rules** — every item stores original URL, publisher, published_at, retrieved_at, content hash, extraction method, and a raw snapshot path.
- **Deduplication strategy** — exact URL/content-hash first, then simhash near-dup, then entity/event clustering; clusters retain all members so syndication can be distinguished from independent corroboration.
- **Scoring** — separate stored signals (watchlist relevance, publisher history, primary-source status, corroboration count, evidence quality); no single opaque LLM credibility number.
- **Summaries** — evidence-linked format: what happened / when / who reported / directly supported / uncertain / changed vs. earlier reporting; every claim cites item IDs.
- **Multimedia staging** — the 5 stages from metadata-only through timestamped transcript chunks and frame embeddings.
- **Agent protocol** — the 9 tools (`list_sources` … `export_log`) over plain HTTP, optionally MCP; thin adapters for OpenAI Agents SDK/LangGraph/etc. later; workflow state always in our own DB.
- **Security & compliance posture** — fetched content is untrusted data (prompt-injection defenses, grounding summaries in stored text); robots.txt/ToS-respecting fetch policy; copyright policy (links + metadata + short excerpts).
- **Retention** — tombstones, revision history, correction tracking.
- **Roadmap M0–M6 + risk register** — success metric: useful items/day via saves/dismissals/corrections/alert interactions.

## 2. Repo scaffold (Python 3.11+, minimal deps)

```
├── docs/DESIGN.md
├── pyproject.toml            # httpx, feedparser, sqlmodel, typer, fastapi, pytest
├── README.md
├── newsdesk/
│   ├── config.py             # DB path, LLM endpoint/key, fetch limits, UA string
│   ├── cli.py                # add-source, list-sources, collect, search, item
│   ├── core/
│   │   ├── models.py         # SQLModel tables: sources, watchlists, items, jobs, log
│   │   └── ids.py            # canonical URL normalization + content hashing
│   ├── storage/
│   │   ├── db.py             # SQLAlchemy engine; SQLite dev, Postgres-ready DSN
│   │   ├── repo.py           # repositories for sources/items/watchlists
│   │   └── fts.py            # FTS5 virtual table maintenance + search
│   ├── ingest/
│   │   ├── fetcher.py        # polite HTTP: robots.txt, per-host rate limit, timeout, retries
│   │   ├── rss.py            # feedparser → RawCapture records
│   │   └── base.py           # Fetcher interface + registry for future adapters
│   ├── pipeline/
│   │   ├── normalize.py      # RawCapture → canonical Item (your JSON shape)
│   │   ├── dedupe.py         # exact URL/hash dedupe; simhash/entity hooks stubbed
│   │   └── runner.py         # idempotent collect job orchestration + immutable log
│   ├── llm/
│   │   ├── base.py           # adapter protocol: extract / embed / rank / summarize
│   │   └── openai_compat.py  # OpenAI-compatible HTTP adapter (GLM/OpenAI/Ollama)
│   ├── api/app.py            # FastAPI: /sources /items /search /watchlists /tools/*
│   └── agents/protocol.py    # 9 tool definitions; 3 wired now, 6 stubbed
└── tests/
    ├── fixtures/feeds/       # bundled RSS/Atom samples → tests run fully offline
    ├── test_normalize.py, test_dedupe.py, test_repo_fts.py, test_cli.py
```

## 3. Working end-to-end slice (this session's code)

1. `newsdesk add-source <rss-url-or-fixture-path>` → registered source.
2. `newsdesk collect` → polite fetch (or read fixture) → raw capture with hash → parse → normalize to canonical items → exact-dedupe → store → FTS index update → append-only log entry.
3. `newsdesk search "energy policy"` → FTS5 results with provenance fields; `newsdesk item <id>` → full canonical JSON.
4. FastAPI mirrors the same operations and exposes `list_sources`, `run_collection`, `search_items` under `/tools/` as the seed of the agent protocol.
5. LLM adapter interface + OpenAI-compatible implementation, with graceful no-key stub mode so the loop runs without credentials.

Key choices: SQLite + FTS5 for zero-setup local dev (storage layer abstracted so Postgres drops in later); no external queue in v1 — jobs table + CLI-triggered runs; dependencies kept to the seven listed above.

## 4. Verification

- `pytest` green, fully offline via fixture feeds (normalize, dedupe, repo/FTS, CLI end-to-end).
- Manual smoke: `collect` against a fixture feed, then `search` returns items with provenance.
- `uvicorn newsdesk.api.app:app` starts and serves `/docs`.

## 5. Explicitly out of scope this session (documented in roadmap)

LLM summarization wiring, similarity clustering, YouTube/media ingestion, transcription/OCR, email/forum ingestion, MCP server packaging, digest/alert delivery, multi-user.