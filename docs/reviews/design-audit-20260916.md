# Design audit — M0/M0.5 scaffold vs DESIGN.md

**Date:** 2026-09-16 (overnight run) · **Scope:** `newsdesk/` + `tests/` audited against `docs/DESIGN.md` and `docs/HARNESS.md`. Report only; no code changed.
**Method:** full read of all 11 subpackages, tests, and both docs; Ousterhout deep/shallow-module lens (complexity = dependencies + obscurity; information hiding; define-errors-out-of-existence).
**Baseline:** `pytest -q` → **73 passed** at audit time. Working tree already contained uncommitted M0.5 work; nothing was modified for this audit.

---

## 1. Verdict

The scaffold is unusually faithful to its design. The runner's per-source failure isolation (§4), the append-only log (§3.1), tier-1 dedup semantics (§6), the politeness contract (§5.2), the accounts capability model (§18), and the single-point prompt-injection defense (§8) all match the spec and are tested. No credentialed scraping, no opaque scores, no multi-tenancy anywhere in the code.

The drift that exists is concentrated in four buckets:

1. **Doc-reference rot** — five docstrings cite DESIGN.md sections that have since been renumbered/rewritten. Cheap to fix, expensive to ignore: the pointers are how future agents re-anchor code to spec.
2. **DESIGN.md lags the code** — §10 still says "nine tools" while 10 are wired; §5.1's rollout order contradicts §15/§18/§19 on what already shipped.
3. **Three genuine contract violations** — a latent schema-registration no-op in `storage/db.py`, a mis-filled `RawCapture.content_hash` in the arXiv fetcher, and "invalid source rejected at add time" (§4 stage 1) not actually implemented.
4. **Shallow-module smells before they calcify** — the snapshot policy is copy-pasted in five fetchers, the canonical record travels as an untyped dict, content-kind knowledge leaks into the pipeline, and the CLI reaches into provider privates.

---

## 2. Findings

### A. DESIGN.md itself has drifted (doc-side drift)

| # | Finding | Evidence |
|---|---|---|
| A1 | §10 says "**Nine** tools" and marks `create_digest` "planned (M4)"; code ships **10** wired/planned specs including `summarize_item` (wired) and `create_digest` (wired). Tests assert 10. | `docs/DESIGN.md:306,317` vs `newsdesk/agents/protocol.py:15-90`, `newsdesk/api/app.py:169-182`, `tests/test_api.py:19` |
| A2 | §5.1 rollout order still places YouTube metadata and transcription at M5, but §15 M0.5 declares "metadata+transcripts+vision shipped"; email/twitter (§18) and arXiv (§19) are absent from the §5.1 list entirely. The doc contradicts itself. | `docs/DESIGN.md:183-193` vs `:395,400,402` |
| A3 | §5.3 fixes the snapshot filename as `..._{hash}.xml`; actual extensions vary by payload (.xml/.json/.atom/.email.json/.x.json). Intent is fine, wording is stale. | `docs/DESIGN.md:212-213` vs `newsdesk/ingest/arxiv.py:111`, `newsdesk/accounts/email.py:171` |
| A4 | §4 stage 8 claims log rows are "atomic with item writes". Implementation commits item and log entry separately, so a crash between them leaves an item with no `item_created` entry (or vice versa for `source_collected` stats). Either make it true (single transaction) or soften the doc. | `docs/DESIGN.md:169` vs `newsdesk/storage/repo.py:107,144,306`, `newsdesk/pipeline/runner.py:93-103` |

### B. Stale doc-reference pointers in code (doc-reference rot)

All five are off-by-one-section errors of the same kind — references written against an older DESIGN.md outline:

| # | Location | Says | Should say |
|---|---|---|---|
| B1 | `newsdesk/core/models.py:103` | canonical record "section 4.2" | §3.2 |
| B2 | `newsdesk/pipeline/normalize.py:3` | canonical shape "section 4.2" | §3.2 |
| B3 | `newsdesk/pipeline/normalize.py:21-23` | relevance "see DESIGN.md section 8" | §7 (relevance/scoring); §8 is summarization |
| B4 | `newsdesk/ingest/fetcher.py:3` | politeness contract "section 6" | §5.2 (§6 is dedup) |
| B5 | `newsdesk/pipeline/dedupe.py:1` | dedup tiers "section 7" | §6 (§7 is ranking) |

### C. Contract violations (code vs its own spec)

| # | Finding | Evidence |
|---|---|---|
| C1 | **Latent bug:** `Database.__init__` comment claims `from .. import core` registers SQLModel models before `create_all` — but `core/__init__.py` is **empty**, so the import is a no-op. Verified: `import newsdesk.storage.db` alone yields zero tables in `SQLModel.metadata`. Every entrypoint (cli/api/mcp) happens to import repo modules first, which masks it; any fresh process that builds `Database` directly creates an empty schema and fails later at first query. | `newsdesk/storage/db.py:38-40`; `newsdesk/core/__init__.py` (0 bytes); repro: `.venv/bin/python -c "import newsdesk.storage.db as db; from sqlmodel import SQLModel; print(SQLModel.metadata.tables)"` → `[]` |
| C2 | **`RawCapture.content_hash` contract broken in arXiv fetcher.** `ingest/base.py:39` defines it as "hash of the raw payload bytes"; rss/video/email/twitter all set `sha256_hex(payload)`. `ArxivFetcher` sets it to the response **ETag header** (empty string when absent) while the real payload digest is computed locally inside `_snapshot` and thrown away. Anything keying on capture content-hash (change detection, tests, future dedup fast paths) silently misbehaves for this kind. | `newsdesk/ingest/arxiv.py:90` vs `newsdesk/ingest/base.py:39`, `newsdesk/ingest/rss.py:89,116` |
| C3 | **"Invalid source rejected at add time" (§4 stage 1) is not implemented.** `SourceRepo.add` accepts any `kind`; unknown kinds surface only at collect time as `error:no-fetcher` (`source_error`). Worse, the valid-kind list is duplicated in three places that disagree: `models.py:37` comment omits `arxiv`; `protocol.py:27` add_source args omit `arxiv`; `cli.py:47` help text has a different set again. | `newsdesk/storage/repo.py:28-40`, `newsdesk/pipeline/runner.py:57-63`, `newsdesk/core/models.py:37`, `newsdesk/agents/protocol.py:27`, `newsdesk/cli.py:47` |
| C4 | `HttpHelper.close()` is not in a `try/finally`; any exception escaping the per-source loop body outside `fetch` (log write, job finish, DB error) leaks the httpx client for process lifetime. | `newsdesk/pipeline/runner.py:52,112` |
| C5 | Caption downloads in the video fetcher use raw `httpx.get` with a hardcoded **`User-Agent: Mozilla/5.0`**, bypassing the politeness helper entirely. Caption CDNs are arguably outside the robots/crawl contract, but identifying as a browser contradicts §5.2's "identified user agent" posture and §11's honesty stance; it also gets no rate limiting. | `newsdesk/ingest/video.py:43-48` vs `docs/DESIGN.md:199-208` |

### D. Shallow-module smells (Ousterhout lens) — fix before they calcify

| # | Smell | Why it matters | Evidence |
|---|---|---|---|
| D1 | **Snapshot policy copy-pasted 5×** (mkdir → timestamp → filename → write → `OSError`→None). Same design decision (§5.3) living in five modules with diverging details. Adding retention/pruning or snapshot verification later means five coordinated edits — classic change amplification / information leakage. | one deep `save_snapshot()` hides the whole policy | `newsdesk/ingest/rss.py:130-142`, `newsdesk/ingest/video.py:197-206`, `newsdesk/ingest/arxiv.py:102-115`, `newsdesk/accounts/email.py:167-175`, `newsdesk/accounts/twitter.py:111-119` |
| D2 | **Canonical record travels as an untyped dict.** `normalize_capture` builds it, `ItemRepo.upsert` re-plucks it with ~20 string keys; the schema exists only by convention. Symptom: three test files hand-build the same dict fixture (each a near-copy). The system's most important interface has no named type. | interface should be simpler to use correctly than to misuse | `newsdesk/pipeline/normalize.py:32-79`, `newsdesk/storage/repo.py:76-146`, `tests/test_dedupe.py:15-31`, `tests/test_digest.py:18-33`, `tests/test_summarize.py:37-50` |
| D3 | **Content-kind knowledge leaks into the pipeline.** `normalize` hardcodes `"video" if source.kind in ("youtube", "video")`; every other kind silently becomes `"article"` — including twitter (should be `post` per the model's own kind list) and email. The mapping belongs to the layer that knows the source (fetcher/capture), not the pipeline. | different layer, different abstraction | `newsdesk/pipeline/normalize.py:35-37`, `newsdesk/core/models.py:84` |
| D4 | **`_FFMPEG_PATH` module-global mutable cache** with an empty-string sentinel; probes once per process, never invalidates, hidden state that tests can't reset cleanly. | obvious code, no hidden globals | `newsdesk/pipeline/summarize.py:173-181` |
| D5 | **CLI `accounts test` reaches into provider privates** (`_imap_connect`, two `_api_get`s). `manager.PROVIDERS` is declared "the single source of truth for the CLI" yet the test command special-cases every provider anyway — the abstraction stops one method short. | shallow facade: interface exposes implementation | `newsdesk/cli.py:277-297` vs `newsdesk/accounts/manager.py:21-25` |
| D6 | **MCP `_run` builds a fresh `Database` (create_all + FTS init) per tool call** and swallows *all* exceptions into `{"error": str(exc)}`; DB handle never closed. Error containment is right; its scope is too broad and the DB lifecycle belongs at startup. | pull complexity downward, but aggregate handling at one top level | `newsdesk/mcp_server.py:42-50` |
| D7 | **Watchlist knowledge is global** — `include_terms()` unions *all* watchlists and the runner scores every item against the union; `WatchlistSource` attachments are write-only. Documented as deferred to M4, so this is accepted debt — but it means "the watchlist is the product object" (§1) is currently not true of the scoring path, and M4 digest work will trip over it if M2/M3 code starts relying on the global behavior. | information hiding: the decision "relevance is global" is leaking into call sites | `newsdesk/storage/repo.py:260-269`, `newsdesk/pipeline/runner.py:51`, `docs/DESIGN.md:15-21` |
| D8 | **Term matching implemented twice with subtly different rules** — `relevance_score` (weights, lowercase both sides) vs digest `_collect`/`_term_matches` (substring, weight only used later for grouping). Same knowledge, two modules; behaviors will diverge the first time someone "fixes" one. | information leakage via invisible convention | `newsdesk/pipeline/normalize.py:16-29`, `newsdesk/pipeline/digest.py:41-44,47-72` |
| D9 | **Incremental state overloads `Source.etag`** with fetcher-specific tokens (`imap-uid:<n>`, `x-since:<id>`). Documented in the model comment, and pragmatic today — but the column's meaning is now "opaque fetcher state", which the name no longer says. Rename or add `state_token` when the Postgres migration touches the schema anyway. | precision of names | `newsdesk/core/models.py:42`, `newsdesk/accounts/email.py:106-110`, `newsdesk/accounts/twitter.py:59-61` |
| D10 | **Knob placement is inconsistent**: timeouts/intervals live in `Settings`, while `VIDEO_DETAIL_CAP`, `SNAPSHOT_TRANSCRIPT_LIMIT`, `BODY_LIMIT`, `SNIPPET_CHARS`, `MAX_LLM_ITEMS` etc. are module constants. Mostly fine (policy constants vs user knobs), but there is no stated rule; the first person to need `NEWSDESK_MAX_VIDEO_ITEMS` will grep twice. | consistency (ch17) | `newsdesk/config.py:19-29` vs `newsdesk/ingest/video.py:32-33`, `newsdesk/pipeline/digest.py:20-22` |
| D11 | `import newsdesk.api.app` runs `create_app()` → opens/creates the database at import time. Standard FastAPI convenience, but it makes the module un-importable for introspection without side effects; uvicorn can also boot from the factory (`newsdesk.api.app:create_app().app` style) if the module-level instance is dropped. | obvious imports | `newsdesk/api/app.py:205` |
| D12 | `GET /tools/daily_digest` **mutates** (appends `daily_digest_built` to the log). Harmless today, but it breaks safe-GET semantics on the tool surface that harnesses will happily re-invoke from previews. | protocol hygiene | `newsdesk/api/app.py:169-173`, `newsdesk/pipeline/digest.py:158-162` |
| D13 | Broad `except Exception: return None/[]` in `fts.search_ids` and keyring reads. Both are documented, deliberate masking of environmental failure (FTS unavailable, no keyring) — acceptable per ch10, but they also mask programming errors; consider catching the specific sqlite/keyring exception types. | masking has a scope | `newsdesk/storage/fts.py:37-38`, `newsdesk/accounts/credentials.py:55-57` |

### E. What already matches the design (do not regress)

- Per-source failure isolation exactly implements the §4 contract: fetcher-missing, FetchError, and unexpected exceptions each become `source_error` + stats; run continues (`runner.py:57-77`).
- `LogRepo` is append-only *by construction* — no update/delete methods exist (`repo.py:296-312`).
- All §5.2 politeness machinery (robots incl. disallow-all skip, per-host rate limit, conditional GET 304, bounded backoff, Retry-After on 429) lives in one helper and is tested (`fetcher.py`, `test_fetcher.py`).
- Injection defense (untrusted `<source>` framing, citation-required output, unparseable-output containment) is enforced in exactly one place, `llm/base.py`, and adapters can't bypass it — the deepest module in the repo.
- Dedup tier-1 semantics (unchanged/revised/duplicate, `duplicate_of` to first non-duplicate twin) match §6 and are proven against cross-feed fixture syndication (`test_dedupe.py`).
- Accounts: credentials never touch DB/log/snapshot; capability grants validated against a known-set; actor-tagged collection matches §18 rules 1-4 (`accounts/base.py`, `manager.py`, `runner.py:24-27`, `test_accounts.py`).
- FTS trigger-maintained with graceful LIKE fallback matches §13 (`db.py`, `fts.py`, `repo.py:192-207`).

---

## 3. Refactor tickets (each scoped to one evening)

Ordering: T1–T2 are bugs (do first); T3–T6 unblock M1/M2; T7–T9 are hygiene. None changes external behavior except where noted.

---

### T1 — Re-anchor all DESIGN.md section pointers (docs + docstrings)

**Files:** `docs/DESIGN.md`, `newsdesk/core/models.py`, `newsdesk/pipeline/normalize.py`, `newsdesk/ingest/fetcher.py`, `newsdesk/pipeline/dedupe.py`.

- Fix the five docstring pointers (B1–B5) to the correct section numbers.
- DESIGN.md: §10 table gains a `summarize_item` row and "nine" → "ten" (A1); §5.1 list annotated with actual status per shipped adapter (A2); §5.3 wording → "extension reflects payload format" (A3); §4 stage 8 reworded to match reality until T2-era atomicity work (A4).

**Mechanical completion check:** `grep -rn "DESIGN.md section" newsdesk/` returns only references whose target heading exists in DESIGN.md (spot-check list in ticket comments); `grep -c '"name"' newsdesk/agents/protocol.py` count matches the §10 table row count; `pytest -q` green; no signature changes.

---

### T2 — Contract repairs: schema registration, arXiv content hash, client lifetime

**Files:** `newsdesk/storage/db.py`, `newsdesk/ingest/arxiv.py`, `newsdesk/pipeline/runner.py`, `tests/`.

1. `db.py`: replace `from .. import core` with `from ..core import models  # noqa: F401` (or import at module top). Add regression test: subprocess `python -c "import newsdesk.storage.db; from sqlmodel import SQLModel; assert SQLModel.metadata.tables"` → non-empty.
2. `arxiv.py`: compute `payload_hash = sha256_hex(payload)` once, use for both `content_hash` and the snapshot filename (C2). Extend `test_arxiv.py` to assert `capture.content_hash == sha256_hex(ATOM)`.
3. `runner.py`: wrap the per-source loop + finalization so `http.close()` runs in `finally` (C4).

**Mechanical completion check:** the three new/updated tests pass; full `pytest -q` green (73+); `grep -n "from .. import core" newsdesk/storage/db.py` empty.

---

### T3 — One snapshot writer instead of five

**Files:** new `newsdesk/ingest/snapshots.py`; edits in `ingest/rss.py`, `ingest/video.py`, `ingest/arxiv.py`, `accounts/email.py`, `accounts/twitter.py`.

- Extract `save_snapshot(settings, *, source_id: int | None, payload: bytes, ext: str, when: datetime | None = None) -> str | None` implementing the §5.3 policy once (best-effort, `OSError` → `None`, same filename grammar `src{id}_{stamp}_{hash8}.{ext}`).
- Replace the five copies, preserving each caller's extension (`.xml`, `.json`, `.atom`, `.email.json`, `.x.json`). No signature changes to any `Fetcher.fetch`.

**Mechanical completion check:** `grep -rn "snapshots_dir" newsdesk/` returns hits only in `snapshots.py` and `config.py`; existing snapshot assertions in `test_normalize.py`, `test_arxiv.py`, `test_video_fetcher.py`, `test_accounts.py` pass unchanged; `pytest -q` green.

---

### T4 — Name the canonical record: `CanonicalItem` in, dict-plucking out

**Files:** `newsdesk/core/models.py` (or new `newsdesk/core/canonical.py`), `newsdesk/pipeline/normalize.py`, `newsdesk/storage/repo.py`, `tests/conftest.py`, `tests/test_dedupe.py`, `tests/test_digest.py`, `tests/test_summarize.py`.

- Introduce a `CanonicalItem` TypedDict (or frozen dataclass) whose fields are exactly the §3.2 shape; `normalize_capture` returns `list[CanonicalItem]`; `ItemRepo.upsert(item: CanonicalItem, ...)` reads typed fields instead of nested dict indexing; add `Item.from_canonical()` so the row-flattening lives next to `to_canonical()` (symmetric pair).
- Move the test dict-factories into one shared `make_canonical_item(...)` helper in `conftest.py`; delete the three private copies.

**Mechanical completion check:** `grep -n '\["provenance"\]\|\["source"\]\|\["content"\]' newsdesk/storage/repo.py` returns no hits; the three test files no longer define local `_make_item`/`_mk_item`/`_video_item` dict builders; `pytest -q` green; `to_canonical()` output byte-identical (existing API tests prove it).

---

### T5 — Source kinds: one list, validated at add time

**Files:** `newsdesk/core/models.py` (or `newsdesk/core/kinds.py`), `newsdesk/storage/repo.py`, `newsdesk/agents/protocol.py`, `newsdesk/cli.py`, `newsdesk/api/app.py`, tests.

- Define `SOURCE_KINDS` (and descriptions) once in `core`; fetcher registration (`ingest.base.register`) asserts `cls.kind in SOURCE_KINDS`; the model comment, TOOL_SPECS `add_source` args, and CLI help all render from it — including `arxiv`, which all three currently miss (C3).
- `SourceRepo.add` rejects unknown kinds with `ValueError` (design §4 stage 1); API maps to 422, CLI prints an error and exits 2, MCP returns an error payload. This is the one deliberate behavior change in the ticket batch.

**Mechanical completion check:** new test asserts `POST /sources {"kind":"bogus"}` → 422 and `add-source --kind bogus` → exit 2; `python -c "from newsdesk.core.models import SOURCE_KINDS; assert 'arxiv' in SOURCE_KINDS"`; `pytest -q` green.

---

### T6 — Content kind belongs to the capture, not the pipeline

**Files:** `newsdesk/ingest/base.py`, `newsdesk/ingest/rss.py`, `newsdesk/ingest/video.py`, `newsdesk/ingest/arxiv.py`, `newsdesk/accounts/email.py`, `newsdesk/accounts/twitter.py`, `newsdesk/pipeline/normalize.py`, tests.

- Add `content_kind: str = "article"` to `RawCapture` (fetchers set `video` for youtube/video captures, `post` for twitter, `document` for email/arxiv — final naming per `models.py:84`); `normalize_capture` reads `capture.content_kind` and stops branching on `source.kind` (D3).

**Mechanical completion check:** `grep -n "source.kind" newsdesk/pipeline/normalize.py` returns no hits; `test_video_fetcher.py` kind assertions still pass; new micro-test asserts twitter items normalize to `post`; `pytest -q` green.

---

### T7 — Retire the `_FFMPEG_PATH` global

**Files:** `newsdesk/media/vision.py`, `newsdesk/pipeline/summarize.py`.

- Move caching into `media.vision` as `@functools.lru_cache(maxsize=1)` over `ffmpeg_exe()`; `summarize.py` calls it directly and loses its module global and empty-string sentinel (D4).

**Mechanical completion check:** `grep -n "_FFMPEG_PATH\|global " newsdesk/pipeline/summarize.py` returns nothing; `pytest -q` green.

---

### T8 — `test_connection()` on every account provider

**Files:** `newsdesk/accounts/email.py`, `newsdesk/accounts/twitter.py`, `newsdesk/accounts/youtube_account.py`, `newsdesk/cli.py`, `tests/test_accounts.py`.

- Each provider exposes one uniform `test_connection(session: AccountSession) -> str` (human-readable identity/status line); `cli.py accounts test` becomes a three-line loop over `PROVIDERS[kind].test_connection` and stops importing provider privates (D5). CLI output text stays equivalent.

**Mechanical completion check:** `grep -n "_imap_connect\|_api_get" newsdesk/cli.py` returns nothing; new test drives `accounts test` for all three kinds against the existing fake IMAP/mock transports; `pytest -q` green.

---

### T9 — MCP server: one Database per process, narrower error containment

**Files:** `newsdesk/mcp_server.py`.

- Build `Database` lazily once per process (env read at first call, matching how tests set env before first tool call); `_run` opens sessions from it and narrows the catch to `(FetchError, AccountError, LLMError, sqlalchemy.Error, OSError)`, letting genuine bugs crash loudly (D6). Optional/lowest priority — the per-call `create_all` is wasteful, not wrong.

**Mechanical completion check:** `test_mcp.py` both tests pass unmodified; one added unit test asserts a second `_run` call does not construct a second engine (patch-count `create_engine` calls); `pytest -q` green.

---

## 4. Explicitly deferred (recorded, not ticketed)

- **Log↔item write atomicity** (A4): fixing for real means transactional scope refactors across runner+repos; revisit with the M2 Postgres move where transaction boundaries change anyway.
- **Per-watchlist scoring** (D7): belongs to the M4 digest milestone by design; do not "fix" earlier or M2/M3 code will codify global behavior the digests then have to unwind.
- **`etag` → `state_token` column rename** (D9): schema churn only alongside the Postgres migration (§13).
- **GET-safe digest endpoint** (D12): decided together with the M4 outputs stage, where POST-bound tool routes get defined anyway.

---

## Postscript — 2026-09-17 cleaning pass (fixed)

Implemented in the overnight refactor, with the characterization tests in
`tests/test_contract_characterization.py` flipped to assert the fixed
contracts: **C1** (models imported in `storage/db.py`), **C2** (arXiv payload
digest), **C3** (`SOURCE_KINDS` in `core.models`, validated in
`SourceRepo.add` — 422/exit-2 at add time; staged kinds still collect-time),
**C4** (per-source containment of normalize/upsert + `http.close()` in
`finally`), **C5** (captions identify with `settings.user_agent`), **T3**
(one snapshot writer, `ingest/snapshots.py`), **T5** (kind list rendered into
protocol/CLI from `SOURCE_KINDS`), **T6** (`RawCapture.content_kind`; twitter
→ `post`, email/arXiv → `document`), **T7** (`_FFMPEG_PATH` global removed),
**T8** (public `test_connection` probes; CLI no longer touches privates; the
swallowed exit-2 bug fixed), plus **D5/D6 partial** (MCP engine disposed per
call), **B1–B5** doc pointers, A1–A4 DESIGN.md drift, and fixture findings
F-1/F-2/F-3 (empty entries skipped at normalize; relative links resolve
against the publisher base; CJK queries fall back to LIKE).
T4 is now half-done: the canonical record has a name (`core.models.CanonicalItem`,
threaded through `normalize_capture` / `ItemRepo.upsert` / `Item.to_canonical`)
and the three hand-built test fixtures share one factory (`tests.conftest.make_canonical_item`);
`ItemRepo.upsert` still reads nested dict keys rather than typed fields — the
remaining slice, scheduled before M2 alongside T9 (one Database per MCP process).
Still deferred, as recorded in section 4: log↔item atomicity (A4 real fix),
per-watchlist scoring (D7), `etag`→`state_token` rename (D9), GET-safe digest
route (D12).
