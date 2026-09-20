# Implement W4: stations

Type: task
Status: resolved
Blocked by: 06, 09, 10, 11

## Question

(HITL build) Implement Stations per ticket-10 decisions and CI fan-out per
ticket-11's recommendation: station table + link tables, per-station publish
targets/identities/GUIDs, (station, date) idempotency, per-station digest
scoping, seed v2 `stations` section, CI update.

Acceptance (measurable):

- ≥2 stations defined via seed; each publishes an independent feed (own token path, identity, GUIDs) and neither deploy disturbs the other's archive;
- idempotency is per (station, date); a same-date re-run of one station no-ops only that station;
- digest ranking uses the station's scoped watchlist terms, not the global list;
- CI runs all stations on one schedule per the research recommendation; `docs/morning-actions.md` updated.

## Answer

Resolved 2026-09-20. W4 shipped. Acceptance proven by the full suite
(444 passed, 2 skipped; 15 new tests):

- **≥2 stations via seed, independent feeds, no cross-disturbance.**
  `seed/newsdesk-seed.json` carries `morning-briefing` (NULL path segment =
  the legacy root, byte-for-byte) and `papers` (path segment `papers`, its
  own identity); imported by CI before every leg and pinned by
  `test_committed_seed_file_defines_the_stations`. Each station publishes
  under its own `<base>/<token>[/<segment>]/` with its own GUIDs and feed
  identity, and re-publishing one leaves the other's `feed.xml` and
  `episodes.json` untouched
  (`test_workflow_stations.py::test_two_stations_publish_independent_feeds_and_neither_disturbs_the_other`,
  `test_publish_cloudflare.py::test_station_deploy_restages_every_stations_archive`).
  On the Pages transport a deploy is the complete site, so each leg fetches
  back and restages **every** station's archive and refuses to ship if any
  of it cannot be fetched (the archive-intact posture widened:
  `test_station_deploy_refuses_when_a_sibling_archive_is_unfetchable`).
- **(station, date) idempotency.** `already_published(settings, date,
  station)` consults only that station's own manifest on both publishers
  (`test_already_published_is_per_station`); the engine's guard and
  `archive_intact` both take the resolved row. A same-date re-run of one
  station returns `already_published` while its sibling still runs
  (`test_same_date_rerun_noops_only_the_station_that_already_published`).
- **Digest ranking uses the station's scope.** `station_scope()` turns the
  row's watchlist binding into F1's `{include_terms, exclude_terms,
  source_ids}` (NULL binding = the global union, no excludes, all sources);
  `candidate_items`/`build_daily_digest` take it, the engine resolves it at
  pre-flight and hands it to context-native select plugins through
  `ctx.scope` — the strategies stay stations-table-blind. A station bound to
  a scoped watchlist speaks only its attached source's items
  (`test_station_scope_is_the_bound_watchlist_not_the_global_union`,
  `test_station_run_ranks_by_its_scoped_watchlist`), and the station-less
  run's digest output is unchanged with or without stations defined.
- **CI + docs.** One schedule, a `morning` matrix over the seed's station
  ids, `fail-fast: false`, `continue-on-error` deliberately absent,
  `NEWSDESK_STATION: ${{ matrix.station }}` as the only per-station env
  (ticket 11's recommendation); `docs/morning-actions.md` documents the
  topology, the adding-a-station recipe, the shared-credential split and the
  per-leg cost. Pinned by
  `test_actions_workflow.py::test_station_matrix_runs_every_seeded_station_with_isolated_legs`
  — the matrix list and the committed seed are one contract.

Decisions taken during the build:

1. **The feed builder recomputed GUIDs instead of reading the manifest's —
   fixed at the root.** `build_feed_xml` called `episode_guid(date)`
   unconditionally, so a station's feed would have emitted
   `morning-briefing-<date>` GUIDs while its manifest stored
   `papers-<date>`; the GUID is the podcast's permanent identity and the two
   surfaces may not disagree. One line —
   `ep.get("guid") or episode_guid(date)` — makes the manifest the single
   source of GUIDs, station and station-less alike. The legacy feed is
   byte-identical (its manifest already held exactly the computed value: the
   characterization and local-publish suites are untouched and green).
2. **`local-dir` writes only its own subtree; the union restage lives in the
   Pages transport only.** A plain directory has no complete-site semantics,
   so a local station run cannot drop, and must not prefix-fetch, its
   siblings — the local publisher is the test double and the archive.
3. **Station resolution is pre-flight, before plugins** (`_resolve_station`,
   tagged `selection`): an unknown or retired station fails loudly having
   done no work, and a station run never falls back to another station or to
   station-less behavior.
4. **GUID prefix and path segment come from the station name/row, not the
   workflow.** `episode_guid(date, station)` keeps `morning-briefing`'s
   pre-station string, so the default station's GUIDs are the ones already
   in listeners' clients.
5. **Sidecars follow the run**: `<morning_dir>/<station>/<date>/…` for
   station runs, the pinned legacy layout for station-less runs (sidecars
   are local artifacts, not permanent URLs, so this is layout, not
   contract).
6. **A pre-W4 format-2 seed file still imports calmly** (absent `stations`
   section = nothing to do) — a station seed never deletes, a changed
   `path_segment` is loud (enclosure URLs are permanent), and retirement is
   monotonic.
7. **No station CLI verbs.** Stations enter through the seed or the agent
   tools (tickets 13/14), matching ticket 09's rubric decision; the CLI only
   gained `--station` / `NEWSDESK_STATION` on `morning`.
- **Code:** `newsdesk/workflow/stations.py` (new: `StationRepo`,
  `station_scope`, `resolve_identity`, `publish_target`),
  `core/models.py` (`stations` table, pin 11→12), `workflow/engine.py`,
  `workflow/strategies.py`, `pipeline/digest.py`, `morning/feed.py`,
  `morning/publish.py`, `morning/cloudflare.py`, `morning/script.py`,
  `morning/orchestrator.py`, `seed.py`, `cli.py`, `config.py`,
  `.github/workflows/morning.yml`, `seed/newsdesk-seed.json`,
  `docs/morning-actions.md`, `CONTEXT.md`. **Tests:**
  `tests/test_workflow_stations.py` (new), `tests/test_publish_cloudflare.py`,
  `tests/test_seed.py`, `tests/test_actions_workflow.py`, `tests/test_cli.py`,
  `tests/test_config_settings.py`,
  `tests/test_contract_characterization.py` (table pin).
