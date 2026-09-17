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

**Status:** ready-for-agent

- [ ] Four registries exist with the ticket-09 defaults (`llm-brief`,
      `edge-tts`, `cloudflare-pages`, notify empty); selecting a not-yet-
      registered plugin fails with a clear message, never a silent no-op.
- [ ] `llm-brief` output matches the ticket-08 structure, budget, and
      selection rules: technical-verdict pool only; hype/tangential excluded;
      deep dive = highest-relevance pool item; headlines = next 3 distinct.
- [ ] Cold open is deterministic for a given date (date-hash template pick).
- [ ] Citation guard: asserted facts reference provided item ids; no
      first-person editorializing beyond the fixed open/close templates.
- [ ] Keyless extractive fallback produces a valid, plainer Script without
      failing the run.
- [ ] Sidecar JSON written with the decided shape; `morning_brief_built`
      log entry records sections + item ids; sidecar is not published.
- [ ] Tests cover structure/budget, selection rules, hash-determinism,
      citation guard, and the fallback.
