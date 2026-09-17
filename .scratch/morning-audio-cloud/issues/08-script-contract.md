Type: grilling
Status: resolved
Blocked by: 04

## Question

Define the spoken-briefing contract the script-writer plugin must produce:
target listen length; structure (how many headlines, how many deep dives,
closing line); language policy when sources mix zh/en; voice choice per
language; and how ADR 0001's signal/hype verdict gates what is spoken (hype
excluded, stored reason). Output: a concrete script-format spec — the
" Shape" section of the script-writer plugin's interface.

## Answer

Decided 2026-09-18. The script-format spec (script-writer plugin's Shape):

**Language & budget.** All-English, proper nouns/technical terms as-is
(Chinese sources are rendered into English by the writer). ~750 spoken
words ≈ 5:00 at the default edge-tts rate. A short candidate day produces a
short episode — never padded.

**Structure.**
1. *Cold open* — one sentence (≤20 words), from a fixed template set of
   ~3 openers chosen by date hash (deterministic, not random).
2. *Headlines ×3* — 2–3 sentences (≤60 words) each: what happened + why it
   matters.
3. *Deep dive* — ≈400–450 words on the day's top signal item: problem →
   novel approach (concrete mechanism) → evidence → why it matters →
   limitations.
4. *Close* — one sentence; full digest has the details.

**Selection rules (deterministic, auditable — enforces ADR 0001).**
- Candidate pool: window items with verdict `technical`. `hype` and
  `tangential` never enter the script; their stored reasons stay in the
  digest.
- Deep dive = highest relevance score among the pool; headlines = the next
  3 distinct items by score. Fewer candidates → shorter episode.
- v1 has no cross-day story memory (syndication dedup already comes from
  the digest's content_hash chains).
- Writer is citation-guarded like the digest prompt: facts asserted must be
  traceable to MaterialPack items; no first-person editorializing beyond
  the fixed open/close templates.

**Sidecar (provenance).** `<date>-script.json`: sections[] with
`{type, item_ids, text, voice, est_seconds}`. It feeds the TTS plugin and
lands in the immutable log (`morning_brief_built`) — it is **not
published** (ticket 07's metadata minimization); only MP3 + feed XML go to
the host.

**Voice (config, not architecture).** Default `en-US-AriaNeural`
single-voice since all-English; if Chinese proper nouns grate, swap to a
multilingual voice — a config line, per the plugin posture.

**Later-if-annoying list:** cross-day story memory; area-diversity rotation
for the deep dive; mixed-language mode.
