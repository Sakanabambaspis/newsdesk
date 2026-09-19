# Strategies + repair policy

Type: grilling
Status: open
Blocked by: 02, 07

## Question

Decide the selection semantics and the loop policy:

- the three launch strategies in terms of rubric scores + verdicts + syndication clusters: `top-k-interesting` (k param), `trending-impactful-mix` (what do "trending" and "impactful" mean measurably?), `single-deep-dive`;
- cluster collapse rule (one story per syndication cluster; cluster size spoken as "covered by N outlets");
- cross-theme diversity floor: formal check spec (what counts as a theme, what is the floor);
- repair-loop policy numbers per check: attempts (≤2?), degrade path (extractive vs fail-loud), what failure notes the re-run sees;
- whether strategy choice is a descriptor param or separate registered plugins per strategy (recommend: plugins).

Answers are the build spec for ticket 09.
