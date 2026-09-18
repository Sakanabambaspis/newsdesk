# 06 — cloudflare-pages publisher (second PUBLISHERS member)

**What to build:** The real host. Per the hosting decision (wayfinder
ticket 02): the publisher uploads the episode MP3 plus the whole-regenerated
feed to a Cloudflare Pages project (direct upload via wrangler/API from the
run — no git remote involved), and returns the permanent public URLs under
the token-prefixed path. Free tier, no egress charges; 25MiB/file limit fits
daily briefings comfortably. Secrets `NEWSDESK_CLOUDFLARE_API_TOKEN` (Pages
edit permission) and `NEWSDESK_FEED_TOKEN` (the path token) enter via env
only and are never logged (DESIGN.md §18 discipline). Publishing is
append-only — a new day adds files and the feed lists all episodes (ticket
10); same-date republish is idempotent.

Verified against a real Pages project on artifacts from a local
`newsdesk morning` run (the registry seam means no orchestration changes are
needed — select it via env).

**Blocked by:** 04 — Podcast feed generator + local-dir publisher

**Status:** needs-human (implementation + mocked tests done; live deploy
against the real Pages project requires credentials only you can create —
exact steps below)

- [ ] Uploading a locally produced episode makes feed + audio reachable at
      the token-prefixed `*.pages.dev` URLs over HTTPS.
- [x] A second day appends: both episodes in the feed, prior URLs/GUIDs
      unchanged; same-date republish doesn't duplicate.
- [x] Returned URLs are the permanent enclosure/feed locations the feed
      actually points at.
- [x] Tokens are read from env, appear in no log line or error message.
- [ ] Upload logic unit-tested with a mocked transport; one live
      verification against the real project is performed and noted.
      (Mocked half done — 16 new tests; the live half needs the setup below.)

## Comments

Implemented 2026-09-18 (frontier worker). 232 tests pass (16 new in
`tests/test_publish_cloudflare.py`), fully offline: the HTTP transport and
the wrangler subprocess are faked, so staging, manifest merge, archive
fetch-back, guard, redaction, and config failure paths are all exercised.

- **`morning/cloudflare.py`** (registered `cloudflare-pages`, now the
  registry default): stages the **complete site** — every episode plus
  regenerated feed/artwork/`episodes.json` under `<token>/…` — and ships it
  with one `wrangler pages deploy` (env `CLOUDFLARE_API_TOKEN` /
  `CLOUDFLARE_ACCOUNT_ID`; secrets ride env, never argv, and are redacted
  from any error tail).
- **Archive protection (the important part):** a Pages deployment is the
  whole site and the host holds the only copy of past episodes (delivery
  ticket 05) — so the publisher fetches every prior episode back from the
  live site before redeploying, and **refuses to deploy** (loud error) if
  any prior episode can't be fetched, rather than silently dropping it from
  the archive (retention ticket 10). `already_published` GETs the remote
  `episodes.json` and fails open (publish stays idempotent per date).
- **Config:** `NEWSDESK_FEED_BASE_URL`, `NEWSDESK_CLOUDFLARE_API_TOKEN`,
  `NEWSDESK_CLOUDFLARE_ACCOUNT_ID`, `NEWSDESK_CLOUDFLARE_PROJECT`,
  `NEWSDESK_WRANGLER_BIN` (default `wrangler`). Missing values fail loudly
  naming the variable, never the value.

### Human setup remaining (the two unticked boxes)

1. Cloudflare dash → Workers & Pages → create a **Pages** project
   ("Direct Upload", empty), e.g. name `morning-briefing`.
2. Note the **Account ID** (dash sidebar).
3. Create an API token with permission **Account → Cloudflare Pages → Edit**.
4. Generate the feed token: `python3 -c "import secrets; print(secrets.token_hex(16))"`.
5. Install wrangler (`npm i -g wrangler`) or set `NEWSDESK_WRANGLER_BIN`.
6. Export `NEWSDESK_FEED_TOKEN`, `NEWSDESK_FEED_BASE_URL=https://<project>.pages.dev`,
   `NEWSDESK_CLOUDFLARE_API_TOKEN`, `NEWSDESK_CLOUDFLARE_ACCOUNT_ID`,
   `NEWSDESK_CLOUDFLARE_PROJECT`, then run `newsdesk morning` (or publish
   today's artifacts directly via `PUBLISHERS.get("cloudflare-pages")`).
7. Verify `https://<project>.pages.dev/<token>/feed.xml` parses (feedparser)
   and the episode plays; then tick the two boxes above and set `Status: done`.
   (Alternatively the ticket-07 rehearsal — a real workflow dispatch — can
   serve as this live verification; tick both then.)

