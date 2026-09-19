# Rubric schema + scorer contract

Type: prototype
Status: open
Blocked by: 01

## Question

What is a Rubric (pure data) and the scorer contract? Decide:

- rubric fields: dimensions, weights, thresholds, anchor examples — rich enough that conversational preference vocabulary ("I like deep technical substance, hate funding hype") compiles into it;
- scorer I/O: per-item score + a stored, inspectable reason (ADR 0001 posture — the model never places an item without one); where scores/reasons are stored;
- the mechanical no-key fallback formula (recency + watchlist relevance + corroboration);
- how a rubric attaches to a descriptor (inline param vs referenced catalog artifact) and whether rubrics are themselves catalog-versioned.

Deliverable: draft rubric JSON + scorer interface + a sample rubric scored
against a fixture item set, shown alongside the extractive fallback.
