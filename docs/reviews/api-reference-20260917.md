# Newsdesk HTTP API reference (as shipped)

**Date:** 2026-09-17 (batch 2) · **Source:** introspected from the running app's OpenAPI schema (`GET /openapi.json`) plus live responses against `tests/fixtures/feeds/` fixtures. Report only.
**App:** `newsdesk.api.app:create_app()` · served by `newsdesk serve` on `http://127.0.0.1:8000` (interactive docs at `/docs`).
**Surface:** 19 routes — 13 user endpoints + 6 agent-tool bindings + 1 discovery route. Portability rules per DESIGN §10: plain HTTP + JSON, harness-independent, all state in the Newsdesk DB.

**Design-coverage key:** each endpoint names the DESIGN.md section it implements, or is flagged **[undeclared in DESIGN.md]** — endpoints that exist in code but appear nowhere in the design doc. Per audit finding A1 (§10's table lags the code), "wired in HARNESS.md only" is flagged separately.

---

## Discovery

### GET /tools
Machine-readable tool spec (name, description, args, status, HTTP binding) for all 10 tools — the §10 discovery contract. Implements **§10**.

```json
["list_sources", "add_source", "run_collection", "search_items", "get_item",
 "get_cluster", "summarize_item", "summarize_cluster", "create_digest", "export_log"]
```
`get_cluster` / `summarize_cluster` carry `"status": "planned (M2/M3)"`.

---

## Health & log

### GET /health — **[undeclared in DESIGN.md]**
Liveness + FTS availability (§13's LIKE-fallback condition surfaced to ops).

```json
{"status": "ok", "fts_enabled": true}
```

### GET /log?limit=50 — **[undeclared in DESIGN.md]** (user variant)
Tail of the append-only log (§3.1 `LogEntry`); newest first. The declared tool binding is `GET /tools/export_log`.

```json
[{"id": 8, "ts": "2026-09-15T17:14:20.231586+00:00", "actor": "system",
  "action": "job_finished",
  "detail": {"job_id": 1, "status": "done",
             "totals": {"created": 3, "updated": 0, "unchanged": 0, "duplicate": 0}}}]
```

### GET /tools/export_log?limit=500
Agent binding for the same data. Implements **§10 `export_log`** (args `since`/`limit`; `since` accepted per the spec but currently ignored by the handler — reads `LogRepo.recent`).
Note (audit D12): like `/log`, this "read" route is the tool surface harnesses call; both are safe GETs here.

---

## Sources

### POST /sources → 201
Register a source; idempotent on (url, kind). Implements **§10 `add_source`** (tool row binds this route directly) and **§4 stage 1**.
Request (`SourceIn`): `url` (required), `kind` (default `"rss"`), `title`, `publisher`, `fetch_interval_minutes` (default 30).

```json
{"id": 1, "url": "https://example.com/feed.xml", "kind": "rss", "title": "Meridian Wire",
 "publisher": null, "enabled": true, "fetch_interval_minutes": 30,
 "last_fetched_at": null, "last_status": null, "created": true}
```
`"created": false` on re-registration. No kind validation yet (audit C3) — invalid kinds succeed here and fail at collect time.

### GET /sources?enabled_only=false — **[user variant; the §10 `list_sources` tool binds `GET /tools/list_sources`]**
Same payload minus the `created` key, plus it.

### PATCH /sources/{source_id}?enabled=true|false — **[undeclared in DESIGN.md]**
Toggle a source's `enabled` flag (§3.1 `Source.enabled`). Quirk: the payload travels as a **query parameter**, not a JSON body. 404 when unknown.

### GET /tools/list_sources
§10 tool binding; calls the same handler as `GET /sources`.

---

## Items

### GET /items?query=&limit=20 — **[undeclared in DESIGN.md]** (user variant)
Without `query`: most recent items (published_at desc, nulls last). With `query`: full-text search (§13 FTS5, LIKE fallback). Returns canonical records (§3.2).

### GET /items/{item_id}
One canonical record — implements **§10 `get_item`** and the **§3.2** canonical shape.

```json
{"id": "item_8803a0fbbf392557ecc1",
 "source": {"publisher": "Meridian Wire",
            "url": "https://meridian-wire.example/stories/power-auction",
            "kind": "article", "author": null},
 "timestamps": {"published_at": "2026-09-14T08:00:00+00:00",
                "retrieved_at": "2026-09-15T17:14:20.202710+00:00"},
 "content": {"title": "Regional power auction clears at record price", "text": "…",
             "media": [], "transcript": null},
 "analysis": {"topics": [], "entities": [], "relevance": 0.0, "claims": []},
 "provenance": {"content_hash": "70146b7c…", "extraction_method": "rss",
                "url_canonical": "https://meridian-wire.example/stories/power-auction",
                "snapshot_path": "…/snapshots/src1_…_70146b7c.xml",
                "simhash": "…", "revision": 0, "duplicate_of": null}}
```
404: `{"detail": "item not found"}`.

### GET /tools/search_items?query=…&limit=20
§10 `search_items` binding; same handler as `GET /items?query=…`. `query` is **required** here (422 without it).

---

## Watchlists — **all four routes [undeclared in DESIGN.md]**

The watchlist is the product object (§1) with a full entity model (§3.1), but DESIGN specifies no HTTP surface; these five routes are code-only.

### GET /watchlists
```json
[{"id": 1, "name": "grid-watch", "description": null}]
```

### POST /watchlists → 201
`WatchlistIn`: `name` (required), `description`. Logs `watchlist_created` (actor `user`).

### POST /watchlists/{watchlist_id}/terms → 201
`TermIn`: `term` (required), `kind` (default `include`; include/exclude/entity/topic per §3.1), `weight` (default 1.0, constrained 0.0–10.0 → 422 outside). Terms are lowercased on storage. 404 for unknown watchlist.

```json
{"id": 1, "watchlist_id": 1, "term": "grid", "kind": "include", "weight": 2.0}
```

### POST /watchlists/{watchlist_id}/sources/{source_id} → 201
Attach a source to a watchlist (idempotent; the association is currently write-only until per-watchlist scoring lands — audit D7). 404 for unknown watchlist; **200-style no-op success for unknown source_id** (only the watchlist is checked).

---

## Collection

### POST /tools/run_collection
One collection pass. Implements **§10 `run_collection`** and the §4 runner contract (per-source isolation, stats, log entries).
Request (`CollectIn`, optional body): `{"source_ids": [1, 2]}` — omit for all enabled sources.

```json
{"job_id": 1, "status": "done",
 "stats": {"totals": {"created": 3, "updated": 0, "unchanged": 0, "duplicate": 0},
           "sources": {"1": {"created": 3, "updated": 0, "unchanged": 0,
                             "duplicate": 0, "skipped": false,
                             "not_modified": false, "error": null}}}}
```
`status`: `done | partial | error` (§3.1 `Job`). Errors are per-source in `stats.sources.<id>.error`; the run itself doesn't fail (§4).

---

## Summaries & digests

### POST /tools/summarize_item
Grounded, evidence-linked summary of one item; **extractive fallback** with no LLM configured. `SummarizeIn`: `item_id` (required), `force` (default false — cached summaries in `analysis.summary` are reused otherwise).
Wired here and in MCP/HARNESS.md, but **absent from DESIGN §10's table** (audit A1); contract per **§8** (untrusted framing, citation-required claims, contained failures). 404 for unknown item.

```json
{"headline": "Regional power auction clears at record price",
 "what_happened": ["Capacity auction for the 2027 delivery year cleared at 94 dollars… (extractive)"],
 "when": "2026-09-14T08:00:00+00:00",
 "who_reported": [{"publisher": "Meridian Wire", "item_id": "item_8803a0fbbf392557ecc1"}],
 "directly_supported": [], "uncertain": ["No LLM configured; this is an extractive…"],
 "changed_vs_earlier": null, "method": "extractive"}
```
With an LLM: `"method": "llm:<adapter-name>"`.

### GET /tools/daily_digest?hours=24&limit=30
The daily briefing (overview, `worth_following` themes with item-ID citations, `also_noteworthy`, `noise`, full ranked item cards). Bound to **§10 `create_digest`** — whose §10 table row still says "planned (M4)" while this route is wired (audit A1); selection logic per §7 relevance, fallback per §8.

Response keys: `overview, worth_following, also_noteworthy, noise, method, generated_at, window_hours, items_in_window, items_considered, items`.
Note (audit D12): this route **mutates** (appends `daily_digest_built` to the log) despite being a GET.

---

## Planned-tool stub

### GET /tools/{tool_name} → 501
Catch-all for §10 tools without bindings (`get_cluster`, `summarize_cluster`):

```json
{"detail": "tool 'get_cluster' is planned; see GET /tools"}
```

---

## Error shapes (global)

| Condition | Status | Body |
|---|---|---|
| Unknown item / source / watchlist | 404 | `{"detail": "item not found"}` etc. |
| Missing/invalid body fields | 422 | FastAPI validation array: `{"detail": [{"type": "missing", "loc": ["body", "url"], "msg": "Field required", …}]}` |
| Planned tool | 501 | `{"detail": "tool '…' is planned; see GET /tools"}` |
| summarize_item on unknown item | 404 | mapped from the `not_found` error dict |

## Flags (summary)

1. **[undeclared in DESIGN.md]**: `GET /health`, `PATCH /sources/{id}`, `GET /items`, all five `/watchlists` routes, `GET /log`. DESIGN §10 declares the *agent tool* surface only; the user-facing HTTP surface has no design home — worth a short §10.1 in DESIGN.md.
2. **HARNESS.md-declared, DESIGN-stale** (audit A1): `summarize_item`, `create_digest` (§10 rows missing / "planned M4" while wired).
3. **Quirks**: PATCH toggle uses a query param; `attach_source` doesn't validate `source_id`; `export_log`'s declared `since` arg is ignored; `daily_digest` is a mutating GET.
