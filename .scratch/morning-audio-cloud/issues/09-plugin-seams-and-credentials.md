Type: grilling
Status: resolved
Blocked by: 05, 08

## Question

Define the plugin seams — the whole point of "everything a plugin, like the
deepseek harness": registries and narrow I/O contracts for script-writer,
tts, publish, and notify (inputs/outputs/stored metadata), consult
`ousterhout-software-design` for depth and `cordis-composability` for
composability vocabulary. Plus the unattended credential setup: which LLM
provider/key, the Actions secret names (extending DESIGN.md §18's env
discipline to CI), and the automatic fallback to the keyless extractive
path when no key is present.

## Answer

Decided 2026-09-18. Four plugins, each a deep module (one narrow call,
engine/deployment choices hidden), registries only at points of known
variability, no information leakage between stages:

- **script-writer** — `SCRIPTWRITERS`, default `llm-brief`. Input:
  `MaterialPack` (per ADR 0001) + the ticket-08 contract. Output:
  `Script` — sections of `{type, text, item_ids, est_seconds}`. Guarantee:
  citation-guarded, technical-verdict items only, budget respected;
  keyless extractive fallback degrades gracefully, never fails.
- **tts** — `TTS_ENGINES`, default `edge-tts` (future: openai, kokoro-onnx).
  Input: `Script` + voice config. Output: MP3 + duration. Guarantee:
  chunked synthesis, 1–3s pacing, exponential backoff on 403/
  NoAudioReceived, per-chunk voice fallback, loud failure on missing
  chunks.
- **publish** — `PUBLISHERS`, default `cloudflare-pages`. Input: MP3 +
  episode metadata. Output: public feed/episode URLs. Guarantee: idempotent
  per date; feed regenerated whole and listing all episodes (ticket 10);
  permanent enclosure URLs; anonymous metadata (ticket 07); tokens from
  env, never logged.
- **notify** — `NOTIFIERS`, ships **empty** (ticket 05): the interface
  exists, `notify(episode_meta)`; zero registered implementations = no-op.
  Telegram/ntfy can bolt on in ~an hour.

**Orchestration:** one command, `newsdesk morning` (collect → digest with
verdicts → script-writer → tts → publish → notify → log). The GitHub
Action invokes exactly this.

**Credentials (§18 extended to CI).** User's existing OpenAI-compatible key:
Actions secret `NEWSDESK_LLM_API_KEY`, plus plain vars `NEWSDESK_LLM_BASE_URL`
and `NEWSDESK_LLM_MODEL`. Swapping providers = three env values. Missing key
⇒ NullAdapter ⇒ extractive briefing still builds and publishes. Added when
publish lands: secret `NEWSDESK_CLOUDFLARE_API_TOKEN`, secret
`NEWSDESK_FEED_TOKEN` (the path token). Log entries record provider and
outcome, never key material. Private repo, no fork-PR surface.
