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

**Status:** ready-for-agent

- [ ] Uploading a locally produced episode makes feed + audio reachable at
      the token-prefixed `*.pages.dev` URLs over HTTPS.
- [ ] A second day appends: both episodes in the feed, prior URLs/GUIDs
      unchanged; same-date republish doesn't duplicate.
- [ ] Returned URLs are the permanent enclosure/feed locations the feed
      actually points at.
- [ ] Tokens are read from env, appear in no log line or error message.
- [ ] Upload logic unit-tested with a mocked transport; one live
      verification against the real project is performed and noted.
