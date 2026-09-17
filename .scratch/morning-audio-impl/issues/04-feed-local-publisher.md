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

**Status:** ready-for-agent

- [ ] Publishing an episode writes MP3 + feed under `<out>/<feed-token>/…`,
      token as a path segment from config/env, never logged.
- [ ] Feed parses as valid RSS 2.0 + itunes (feedparser or equivalent) and
      carries the required itunes tags, duration, guid, pubDate, enclosure.
- [ ] Metadata is anonymous/generic per ticket 07; no item lists or show
      notes anywhere in the XML.
- [ ] Re-running the same date yields exactly one episode for that date;
      GUIDs and enclosure URLs of prior episodes are unchanged.
- [ ] Feed lists every published episode (append-only; no pruning).
- [ ] Artwork asset ships at 1400–3000px.
- [ ] Tests cover idempotency, append behavior, URL/GUID stability, and
      metadata minimization.
