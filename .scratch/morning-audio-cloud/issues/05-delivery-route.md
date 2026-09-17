Type: grilling
Status: resolved
Blocked by: 02, 03

## Question

Pick the end-to-end delivery architecture: hosting (from 02) + notification
path (from 03) + the phone app(s) to set up on iOS and Android. Must satisfy:
free-only, both platforms, "wake up → tap notification → listening within
seconds". Explicitly decide whether the podcast feed and a notification poke
(e.g. ntfy) are combined, and what the single source of truth for episodes is.

## Answer

**Feed-only, decided 2026-09-18.** ("Do not solve a problem we don't yet
have.") The bar is "ready before I wake up", not punctual delivery — so the
poke leg is dropped.

- **Architecture:** collect → digest → script → TTS → publish (MP3 + RSS to
  Cloudflare Pages per ticket 02, 128-bit token as a path segment, permanent
  URLs). Podcast apps are the only clients and the feed is the single source
  of truth. Secrets: three total (Cloudflare token, feed token, LLM key).
- **Apps per device (recommended, final pick at setup time):** iPhone —
  Apple Podcasts (add-by-URL; notifies + auto-downloads by default).
  Android — AntennaPod (streams by default; reconfigure its 12h refresh to
  ≤1h) or Pocket Casts (paste feed URL; enable notifications once).
- **Notify plugin: deferred, seam kept.** The notify registry slot ships
  empty (interface defined in ticket 09); a poke (Telegram/ntfy, both
  researched) can be bolted on in ~an hour if a missed morning ever proves
  the need.
- **Failure visibility (input to ticket 06):** no routine success
  notification by design; GitHub's automatic workflow-failure emails become
  the error channel.
- **Consequence:** with Telegram's in-chat copy gone, the host holds the
  only copy of every episode → retention/pruning is now a real question
  (graduated as ticket 10).
