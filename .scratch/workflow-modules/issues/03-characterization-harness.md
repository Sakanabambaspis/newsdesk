# Characterization harness for the default chain

Type: task
Status: resolved
Blocked by:

## Question

(AFK, no decision) Pin the current pipeline's observable behavior offline, so
the W1 engine rewrite is provably behavior-preserving — the repo's
characterization-first methodology (`docs/reviews/fixture-findings-20260917.md`).

Work: characterization tests over the existing chain (`orchestrator.run_morning`
and its stage functions) using fixture feeds/DB per `tests/` conventions:

- digest shape for a fixture window (`build_daily_digest` keys, sections, counts);
- script sidecar sections/stats for a stubbed LLM adapter (extractive and llm paths);
- audio report shape for a stubbed synth; publish report + manifest for `local-dir`;
- full-run report JSON and the log-entry sequence for the default chain.

Acceptance (measurable): the suite runs with no network and no LLM key; it
pins the default chain's observable outputs; it is red-capable if any stage's
behavior changes. Findings land in `tests/` as normal pytest files.

## Answer

Landed as `tests/test_characterization_default_chain.py` (6 tests, fully
offline: file:// fixture feed, keyless NullAdapter plus a stubbed adapter at
the scriptwriter seam, synthetic MP3 frames behind the edge-tts `engine`
seam, local-dir publisher). Pins: exact digest dict/fallback prose/material
pack (stats flattened, not nested) and its log entry; the extractive sidecar
verbatim (including the writer's doubled title in extractive fills) and the
stubbed-LLM sidecar; the audio report + per-chunk audio sidecar (5 chunks →
319 frames → 7.7 s); the publish report contract in both host modes
(file-URI and base-URL) plus the manifest entry (int-truncated duration,
`morning-briefing-<date>` GUID, +08:00 stamp); the full-run report's six
stage payloads and the exact 12-entry log sequence.

Acceptance verified: passes with no network/key (and with bogus
LLM/token env vars set — conftest builds Settings from kwargs); red-capability
mutation-checked (CHUNK_SENTENCES 2→3 and WORDS_PER_MINUTE 150→149 each flip
their pins red, then reverted). Suite-wide: 301 passed.

Deliberate calls: exact prose is pinned on purpose (characterization
methodology — copy changes go red and are flipped deliberately); the digest
window uses factory items, not fixture-feed content, because the feed's
hardcoded pubDates age out of the 24h window (the full-run test still pins
the feed being collected and excluded); no socket-blocking guard was added —
offline-ness is by construction, not enforced.
