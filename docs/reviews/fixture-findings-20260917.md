# Fixture findings — bugs exposed by the synthetic fixtures (2026-09-17)

Batch-2 deliverable. The synthetic fixtures under `tests/fixtures/feeds/synthetic-*.xml`
were run through the real fetch → normalize → dedupe → storage pipeline. Where they
exposed real bugs, **the code was left untouched**; each finding is pinned by a
characterization test in `tests/test_synthetic_feeds.py` / `tests/test_fixture_staged_kinds.py`
so the next refactor (batch 1 tickets T2/T4/T5) can flip the assertion to the
expected behavior deliberately.

Severity: F-1 medium (silent data loss), F-3 medium (silent search miss), F-4 low
(contained blast radius), F-2 low (local imports only).

---

## F-1 — Content-empty items from different URLs collide as duplicates

**File/behavior:** `newsdesk/core/ids.py` `content_hash()` + `newsdesk/storage/repo.py` `ItemRepo.upsert` duplicate path.
**Input:** `synthetic-bare.xml` — two genuinely unrelated items with a `<link>`/`<guid>` but no title and no description (headline-only feeds, failed extractions, enclosure-only podcast items).
**Expected:** two distinct `created` items; empty content should not constitute identical content.
**Actual:** both normalize to `title="" text=""`, so `content_hash("\n")` is identical (`01ba4719…`), and tier-1 dedup classifies the second as `duplicate` linked via `duplicate_of` to the first. Every future content-empty item in any feed will silently merge into this one equivalence class, hiding real stories from digests and corrupting the corroboration count (§6).
**Suggested direction:** in `normalize_capture`, skip entries whose title AND text AND transcript are all empty (they carry nothing to anchor a claim to), or give `content_hash` a salt that includes the canonical URL when the body is empty.
**Pinned by:** `tests/test_synthetic_feeds.py::test_bare_items_collide_as_duplicates`.

## F-2 — Relative `<link>`s in locally-imported feeds resolve against the local file path

**File/behavior:** `newsdesk/ingest/rss.py` (`urljoin(source.url, link)` for scheme-less links) with the local-fetch path.
**Input:** `synthetic-rss.xml` item 3, `<link>stories/relative-path</link>`, fetched as a `file://` URI.
**Expected:** the site-relative link resolves against the publisher's web base (the channel `<link>` element, i.e. `https://meridian-wire.example/stories/relative-path`).
**Actual:** it joins against the `file:///…/tests/fixtures/feeds/stories/relative-path` source URL, producing an item whose provenance URL points into the reader's own filesystem. `canonical_url` then keeps it verbatim (no host → returned as-is).
**Impact:** low — only affects manual/local imports; remote feeds arrive with absolute links. But the stored item's URL is silently wrong rather than flagged.
**Suggested direction:** when the source URL is local, resolve relative links against `capture.meta["feed_link"]` when present, else skip the entry (no anchorable provenance).
**Pinned by:** `tests/test_synthetic_feeds.py::test_relative_link_resolves_against_local_path`.

## F-3 — CJK/unsegmented-script full-text search silently returns zero hits

**File/behavior:** `newsdesk/storage/db.py` FTS5 tokenizer (`porter unicode61`) + `newsdesk/storage/fts.py::search_ids` + `newsdesk/storage/repo.py::search` fallback rule.
**Input:** `synthetic-i18n.xml` Japanese item titled 「再生可能エネルギーの導入が過去最高を更新」; query `再生可能`.
**Expected:** a hit — the query is an exact substring of the stored title and text.
**Actual:** `[]`. unicode61 has no CJK word segmentation, so the whole title run indexes as one long token; a shorter query cannot match it. The LIKE fallback never triggers because `search_ids` returns an honest-but-wrong empty list (fallback fires only on `None`, i.e. FTS unavailable/syntax error). Latin and RTL whitespace-delimited scripts in the same fixture (German, Arabic) search correctly, isolating the problem to unsegmented scripts (CJK, Thai, Lao).
**Suggested direction:** for queries containing CJK/codepoint ranges, quote each character into a bigram `OR` query, or run the LIKE path in addition to FTS when the FTS result is empty and the query contains unsegmented-script characters.
**Pinned by:** `tests/test_synthetic_feeds.py::test_i18n_content_roundtrip_and_fts`.

## F-4 — arXiv capture `content_hash` is the ETag header, empty when absent (manifestation of audit finding C2)

**File/behavior:** `newsdesk/ingest/arxiv.py:90` vs the `RawCapture.content_hash` contract ("hash of the raw payload bytes", `ingest/base.py`).
**Input:** `synthetic-arxiv.atom` served over a mock transport **without** an `ETag` header (export.arxiv.org's actual behavior for query endpoints varies).
**Expected:** `content_hash == sha256_hex(payload)`.
**Actual:** `content_hash == ""`. The payload digest *is* computed inside `_snapshot` for the filename and then discarded.
**Blast radius (verified):** contained at the item level — normalize derives per-entry hashes from title+text, so arXiv items still dedupe to `unchanged` across identical responses (`test_arxiv_items_still_dedupe_correctly_across_runs`). The capture-level hash is currently only consumed by tests, but it is a contract violation waiting for the first caller that trusts it (e.g. skip-parsing-if-unchanged fast paths).
**Suggested direction:** batch-1 ticket T2 (compute the payload digest once, use for both capture hash and snapshot name).
**Pinned by:** `tests/test_fixture_staged_kinds.py::test_arxiv_capture_content_hash_is_empty_without_etag`.

---

### Non-findings (checked, behaved correctly)

- Atom republished verbatim at a different URL → correctly `duplicate`, linked to the wire original; copy keeps the serving outlet as publisher.
- Double-escaped entities (`&amp;amp;`), CDATA-wrapped HTML, `&nbsp;`, numeric refs (`&#8212;`, `&#8226;`), quoted attribute values — all decode exactly one layer per format, script/style content stripped.
- `media:content` (YouTube-style video feeds) and podcast `<enclosure>` map into `content.media[]` with url/type/length preserved.
- German/Arabic FTS phrase search works; parameter ordering survives canonicalization; second collection pass is fully idempotent (18/18 `unchanged`, zero new rows).
- Multi-line arXiv `<title>` folds to a single-spaced one-liner; multiple `<author>` entries join with `", "`.

---

### Postscript — 2026-09-17 (fixed)

F-1: `normalize_capture` now skips entries whose title, text, and transcript
are all empty (nothing to anchor a claim or hash to). F-2: relative links in
locally-imported feeds resolve against the feed's own `<link>` publisher base,
else the entry is skipped. F-3: an empty FTS result for a query containing
unsegmented-script characters (CJK, Thai) falls back to substring matching
(`storage.fts.has_unsegmented_script`). F-4: fixed with C2. All pinned tests
in `test_synthetic_feeds.py` / `test_fixture_staged_kinds.py` were flipped to
the expected behavior.
