# Station model + publish topology

Type: grilling
Status: resolved
Blocked by: 04

## Question

Decide the Station entity and its publishing topology:

- Station fields: name, description, scope (sources/watchlists), workflow ref (`name@version` or latest), feed identity (title/author/category/artwork), publish-target params;
- topology: per-station token path under the existing Cloudflare Pages project (recommended) vs alternatives; credential story per station (§18: env-only);
- station-scoped episode identity: GUIDs (`station-<date>`?), idempotency per (station, date), sidecar/publish layout;
- parametrizing `morning/feed.py` constants (title/author/category/language/artwork) without breaking the existing single feed;
- digest scoping to the station's watchlists/sources (lands F1; fixes global include-terms);
- the CONTEXT.md glossary entry for Station (new term, per Episode's promised successor wording).

Answers are the build spec for ticket 12.

## Answer

Decided 2026-09-20. One guiding constraint drives most of it: GUIDs and
enclosure URLs are permanent by publish contract (`publish.py` module
docstring), so the existing single feed must keep its exact URLs forever —
stations are added *around* the legacy feed, never by moving it.

**Station fields** — a `stations` table with the catalog tables' conventions
(retire-never-delete, required actor on every mutation, `station_*` log
actions with `via` origin, no credentials in the table ever):

- `name` — PK, slug `^[a-z0-9][a-z0-9-]{0,63}$`. The one identity: CLI arg,
  GUID prefix, log field. **Immutable once the station's manifest has any
  episode** (a rename would orphan permanent GUIDs); enforced loudly.
- `description` — nullable.
- `workflow_ref` — string, default `"default-morning"`. Bare name floats at
  run start, `name@N` pins: exactly ticket-04 resolve semantics, loud
  pre-flight failure on missing/retired.
- `watchlist_id` — FK, nullable. NULL = the global include-terms union
  (today's `WatchlistRepo.include_terms()`); set = that one watchlist's
  terms. One watchlist per station — source scoping rides the watchlist's
  `watchlist_sources` attachment (none attached → all sources, F1's
  fallback), so no station↔source link table and no double bookkeeping.
- `path_segment` — unique, nullable. NULL = the legacy root path
  `<base>/<token>/…` (the default station only, fixed at bootstrap);
  otherwise `<base>/<token>/<path_segment>/…`. **Immutable** — enclosure
  permanence.
- Feed identity: `feed_title`, `feed_description`, `feed_author`,
  `feed_category`, `feed_language` (default `"en"`), `feed_owner_email`
  (NULL → env/fallback as today). All mutable — the feed is regenerated
  whole on every publish, and metadata changes don't touch GUIDs/URLs.
  NULLs fall back to `feed.py`'s module constants so the seeded default
  station reproduces today's feed byte-for-byte.
- `retired_at` — nullable, monotonic (ticket-04 semantics): retired
  stations refuse runs at pre-flight, the published archive is never
  touched, seed import never un-retires.
- No artwork column: every station ships the generated neutral PNG in v1;
  per-station artwork is fog (map "Not yet specified").
- No publisher column either: the publisher plugin stays the global
  `NEWSDESK_PUBLISHER` knob — all stations share one host kind (one Pages
  project). The only per-station publish-target param is `path_segment`.

**Topology + credentials** — per-station token paths under the existing
Cloudflare Pages project, as the ticket recommended. One project, one shared
`NEWSDESK_FEED_TOKEN` (unchanged) — possession of the tokenized URL stays
the only access control, and every station sits inside it. Per-station
secrets: none; per-station env: only `NEWSDESK_STATION`. Rejected:
dynamic secret-name lookup (`secrets.<prefix>__<station>` — impossible in
GH matrix expressions, N rotations for nothing); separate Pages projects per
station (N domains/deploy configs buys nothing at N=2–5).

The one real consequence of "a Pages deployment is the complete site": with
the CI matrix (ticket 11) each station leg deploys alone, so **every
station's publish stages the union of all stations' archives** — fetch back
every known station's manifest and audio (including the legacy root's)
before `wrangler pages deploy`, exactly as today's single-station fetch-back
generalizes. The station list comes from the stations table (the CI leg
seeds first). Refusing a deploy that would drop any station's history is
the same loud archive-intact posture, widened. The `local-dir` publisher is
a plain directory, not complete-site semantics: each station writes only
its own subtree, no cross-fetch.

Station selection: `newsdesk morning --station <name>`, else
`NEWSDESK_STATION`, else **station-less** — and station-less stays today's
behavior byte-identically (global terms, legacy paths, characterization
pins untouched). A station-less run never reads the stations table.

**Episode identity** — GUID is `<station-name>-<date>` with
`isPermaLink="false"`; for `morning-briefing` that is byte-identical to
today's `morning-briefing-<date>`. No UUIDs: the scheme is already
permanent per (station, date) and human-debuggable. Idempotency is per
(station, date): `already_published(settings, station, date)` checks only
that station's own manifest, so a same-date re-run no-ops exactly one
station (ticket-12 acceptance) and a matrix re-run heals only the missing
stations (research finding 6). Sidecar layout: station runs write
`<morning_dir>/<station>/<date>/<date>-script.json`; station-less runs keep
`<morning_dir>/<date>/…` pinned. Sidecars are local artifacts, not
permanent URLs — free to move.

**Feed parametrization** — `build_feed_xml` gains an identity argument
(title/description/author/category/language); absent → today's module
constants, byte-identical output (characterization-safe). `episode_guid`
gains the station name; station-less callers keep the legacy string. The
default station is seeded with today's exact identity values, so even
station-addressed it publishes the same feed.

**Digest scoping (lands F1)** — `candidate_items` and `build_daily_digest`
gain an optional scope (include/exclude terms + attached-source filter,
`watchlist_id`-shaped); default stays the global union, so the D7 debt is
retired *for station runs only* and the legacy path is preserved.
Strategies stay stations-table-blind: the engine resolves the station row
at pre-flight (`selection` tag, loud on missing/retired), keeps
`ctx.station` as the log-safe name string, and puts the resolved scope on
the RunContext; select plugins thread it into `candidate_items`. F1's
per-publisher cap ("max 3 per publisher per section") is **deferred** —
not in W4 acceptance; fog-noted. Notify stays global in v1 (per-station
routes are already fog).

**Seed v2 + glossary** — seed gains a `stations` section: name-keyed
documents with the identity indirection (`workflow` ref string,
`watchlist` name, `path_segment`, `feed` identity object, `retired` flag);
import idempotent on name with parsed-equality conflict → loud `SeedError`;
retirement round-trips monotonically; the seed ships the default station
`morning-briefing` (`path_segment: null`, today's identity, watchlist
"AI research directions"). CONTEXT.md lands the **Station** entry and
Episode's promised successor wording (exactly one episode per
**(station, date)**) in this resolution commit — decided vocabulary, not
build notes.

Build seam summary for ticket 12: `stations` table + contract pin bump;
`StationRepo` with required-actor mutations; seed v2 `stations`; engine
pre-flight station resolve + ctx scope; `candidate_items`/`build_daily_digest`
scope params; `build_feed_xml`/`episode_guid`/`already_published`/publisher
generalization (union-fetch deploy); `--station`/`NEWSDESK_STATION`; CI
matrix per ticket 11; `docs/morning-actions.md` update.
