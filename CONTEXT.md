# CONTEXT — domain glossary

Glossary only. No implementation details, no decisions, no spec content.

## Episode

The audio rendering of one daily digest. There is exactly one episode per
(station, date): an episode belongs to a station and consumes whatever that
station's workflow selects. For the default station, "episode" and "the
morning briefing" are still the same thing.

## Station

One published podcast surface: a named scope (one watchlist and, through it,
its sources), one workflow binding, one feed identity, one publish path.
Exactly one episode per (station, date). The pre-station single feed is the
default station, not a special case.
