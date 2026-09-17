# 02 — Plugin registries + `llm-brief` script-writer + script sidecar

**What to build:** "Everything a plugin" becomes real. The four registries —
SCRIPTWRITERS, TTS_ENGINES, PUBLISHERS, NOTIFIERS — land as the seam layer
decided in wayfinder ticket 09 (registries only at points of known
variability; each plugin a deep module with one narrow call). Their first
real consumer is the default `llm-brief` script-writer: it turns a
MaterialPack into the spoken-briefing contract of wayfinder ticket 08 —
a `Script` made of sections `{type, text, item_ids, est_seconds}` (that shape
is the decided contract, from the ticket-08/09 answers): a cold open chosen
by date hash from a fixed template set, three headlines (≤60 words each:
what happened + why it matters), a 400–450-word deep dive on the day's top
technical item (problem → novel approach → evidence → why it matters →
limitations), and a one-sentence close. ~750 spoken words ≈ 5:00 budget;
a short candidate day produces a short episode, never padded. The writer is
citation-guarded (facts traceable to MaterialPack items) and falls back to a
keyless extractive briefing that still produces a valid Script. The sidecar
`<date>-script.json` is written for the TTS stage and logged
(`morning_brief_built`); per ticket 07 it is never published.

Demoable on its own via a new `newsdesk script` staging command (same spirit
as the existing `summarize`/`digest` commands).

**Blocked by:** 01 — Digest verdict pass + MaterialPack (ADR 0001 B1)

**Status:** done

- [x] Four registries exist with the ticket-09 defaults (`llm-brief`,
      `edge-tts`, `cloudflare-pages`, notify empty); selecting a not-yet-
      registered plugin fails with a clear message, never a silent no-op.
- [x] `llm-brief` output matches the ticket-08 structure, budget, and
      selection rules: technical-verdict pool only; hype/tangential excluded;
      deep dive = highest-relevance pool item; headlines = next 3 distinct.
- [x] Cold open is deterministic for a given date (date-hash template pick).
- [x] Citation guard: asserted facts reference provided item ids; no
      first-person editorializing beyond the fixed open/close templates.
- [x] Keyless extractive fallback produces a valid, plainer Script without
      failing the run.
- [x] Sidecar JSON written with the decided shape; `morning_brief_built`
      log entry records sections + item ids; sidecar is not published.
- [x] Tests cover structure/budget, selection rules, hash-determinism,
      citation guard, and the fallback.

## Comments

Implemented 2026-09-18 (frontier worker). 183 tests pass (10 new).
Demo: `newsdesk script` stages digest → script → sidecar on real data
(verified locally: 6 sections, sidecar at `~/.newsdesk/morning/<date>/`).

- **Registries (`morning/registries.py`):** one small `Registry` class; the
  four slots carry the ticket-09 default names; `get()` raises a clear
  KeyError for unregistered names (edge-tts/cloudflare-pages are named
  defaults whose plugins land in 03/06). `load_plugins()` imports built-in
  plugin modules (acyclic registration); notify ships empty.
- **Selection is code, not model:** the pack arrives technical-only and
  rank-ordered (ticket 01), so deep dive = pool[0], headlines = pool[1:4];
  the LLM only renders prose for assigned items in one call. Cold open/close
  are fixed templates (open picked by sha256(date) hash) — the model never
  writes them, so no editorializing is possible.
- **Guards:** budgets enforced mechanically by sentence-boundary clipping
  (60-word headlines, 460-word deep-dive cap); model output mapped back by
  assigned id only — unknown ids dropped, unusable sections fall back
  extractively per-section; method label is honest (`llm:<name>` only when
  the model contributed something usable, else `extractive`).
- **Pack now embeds in the digest:** `material_pack.items` (full cards with
  text) is part of the briefing, making the writer's exact input auditable
  (ADR 0001's first-class artifact). Additive to ticket 01's summary.
- **Episode dates are HKT** (`Asia/Hong_Kong`), matching the 07:07 HKT
  schedule; `episode_date()` normalizes any timestamp.
- **For ticket 03 (edge-tts):** consume the sidecar sections
  (`{type, text, item_ids, voice, est_seconds}`); voice is
  `en-US-AriaNeural` via `script.DEFAULT_VOICE` — make it configurable there.
- **Note:** the no-verdict fallback (no LLM) means relevance order rules the
  pool, so a high-relevance hype item can headline a labeled-extractive
  script. Accepted: the label says so; verdicts arrive with the LLM key.

