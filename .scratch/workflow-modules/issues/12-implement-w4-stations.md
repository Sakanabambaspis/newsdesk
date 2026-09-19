# Implement W4: stations

Type: task
Status: open
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
