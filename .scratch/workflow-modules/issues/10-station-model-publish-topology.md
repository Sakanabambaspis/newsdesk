# Station model + publish topology

Type: grilling
Status: open
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
