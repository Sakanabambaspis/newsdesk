# 04 — Podcast feed generator + local-dir publisher (first PUBLISHERS member)

**What to build:** The publish contract gets proven host-independent. The
`local-dir` publisher writes the episode MP3 plus a whole-regenerated
`feed.xml` into a local output directory, behind a 128-bit random token as a
**path segment** (never a query string — ticket 02's finding). The feed is a
podcast-app-ready RSS 2.0 + itunes feed with the anonymous generic metadata
of ticket 07: feed title "Morning Briefing", date-only episode titles,
pseudonymous itunes:author, alias owner email (Apple requires one), neutral
artwork at 1400–3000px, and **no digest item lists or show notes** — a
scraped feed reveals nothing. Enclosure URLs and GUIDs are permanent and
never reused; the feed lists all episodes (ticket 10: append-only, no prune
code); episode duration comes from the TTS stage. Republishing the same date
is idempotent — today's episode is replaced, not duplicated.

This publisher doubles as the test double for the orchestration and rehearsal
tickets, and stays useful as a local archive/fallback.

**Blocked by:** 02 — Plugin registries + `llm-brief` script-writer + script sidecar

**Status:** done

- [x] Publishing an episode writes MP3 + feed under `<out>/<feed-token>/…`,
      token as a path segment from config/env, never logged.
- [x] Feed parses as valid RSS 2.0 + itunes (feedparser or equivalent) and
      carries the required itunes tags, duration, guid, pubDate, enclosure.
- [x] Metadata is anonymous/generic per ticket 07; no item lists or show
      notes anywhere in the XML.
- [x] Re-running the same date yields exactly one episode for that date;
      GUIDs and enclosure URLs of prior episodes are unchanged.
- [x] Feed lists every published episode (append-only; no pruning).
- [x] Artwork asset ships at 1400–3000px.
- [x] Tests cover idempotency, append behavior, URL/GUID stability, and
      metadata minimization.

## Comments

Implemented 2026-09-18 (frontier worker). 192 tests pass (9 new). Demo
verified: two episodes published with production-style env → clean
`https://<host>/<token>/audio/<date>.mp3` URLs and a bozo-free feedparser
parse, newest-first.

- **`morning/feed.py`** owns the anonymous feed artifact: whole-feed
  regeneration from the manifest (newest first), itunes tags
  (image/category/owner/explicit/duration), date-only titles, permanent
  GUIDs `morning-briefing-<date>` (isPermaLink=false), RFC-2822 pubDate.
  Artwork is code-generated (stdlib PNG writer, 1400×1400 neutral solid) —
  no binary asset in the repo.
- **`morning/publish.py`** (`local-dir`, first PUBLISHERS member):
  idempotent per date (audio replaced, first-published timestamp kept so
  pubDate/GUID are stable), append-only across dates, no prune code.
  Manifest `episodes.json` holds host-neutral rows only.
- **URL design (emerged here, serves 06 too):** `NEWSDESK_FEED_BASE_URL` —
  when set, feed/enclosure URLs are `<base>/<token>/…` (the production
  posture, no local paths in the XML); when unset, local file URIs for the
  archive case. The tests caught a real leak class here: file URIs embedded
  machine paths into the feed, so the anonymity test pins base-URL mode.
- **Config:** `NEWSDESK_FEED_TOKEN` (required, loud failure with generation
  hint), `NEWSDESK_FEED_BASE_URL`, `NEWSDESK_FEED_OWNER_EMAIL` (alias for
  Apple's owner email, generic fallback).
- **For ticket 05:** publish via `PUBLISHERS.get("local-dir")` /
  `cloudflare-pages`; log the outcome without URLs or tokens (URLs embed the
  token by design — that is the only place it appears).
- **For ticket 06 (cloudflare-pages):** reuse `feed.build_feed_xml` +
  `episode_guid`; replace only the transport (upload MP3 + feed + artwork to
  the Pages project) and return the same URL contract.

