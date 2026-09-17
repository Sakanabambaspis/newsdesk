# 03 — edge-tts plugin (first TTS_ENGINES member)

**What to build:** The `edge-tts` engine turns a Script into a listenable
MP3, unattended. Per the wayfinder ticket-04 findings (including the live
smoke test already run on this machine): synthesis is chunked by script
section/paragraph with 1–3s pacing between requests, retries 4–5× with
exponential backoff on the known failure modes (403, NoAudioReceived), falls
back to a per-chunk alternate voice if a voice request keeps failing, and
fails loudly — never silently — if any chunk is missing, so an episode is
never published with a gap. Voice comes from config (default
`en-US-AriaNeural`, all-English per ticket 08). Output: MP3 plus its measured
duration (which the publish stage uses for the feed).

edge-tts is an optional dependency (extras pattern already used for
media/vision/mcp/accounts).

**Blocked by:** 02 — Plugin registries + `llm-brief` script-writer + script sidecar

**Status:** ready-for-agent

- [ ] Given a sidecar Script, the plugin produces a complete playable MP3 and
      its measured duration.
- [ ] Chunked synthesis with pacing; retries with exponential backoff on
      403/NoAudioReceived; per-chunk voice fallback.
- [ ] A missing chunk after retries aborts loudly (non-zero, clear error) —
      no silent gap in the audio.
- [ ] Voice configurable; default `en-US-AriaNeural`.
- [ ] Network-dependent behavior unit-tested with a fake engine; an optional
      live check documented (skipped when offline).
