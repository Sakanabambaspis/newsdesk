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

**Status:** done

- [x] Given a sidecar Script, the plugin produces a complete playable MP3 and
      its measured duration.
- [x] Chunked synthesis with pacing; retries with exponential backoff on
      403/NoAudioReceived; per-chunk voice fallback.
- [x] A missing chunk after retries aborts loudly (non-zero, clear error) —
      no silent gap in the audio.
- [x] Voice configurable; default `en-US-AriaNeural`.
- [x] Network-dependent behavior unit-tested with a fake engine; an optional
      live check documented (skipped when offline).

## Comments

Implemented 2026-09-18 (frontier worker). 209 tests pass (17 new in
`tests/test_edgetts.py`, incl. 4 CLI-surface tests). Demo:
`newsdesk audio` synthesizes the staged sidecar into
`<morning_dir>/<date>/<date>.mp3` and logs `morning_audio_rendered`
(verified live: 2 chunks, 4.3 s measured, 26 KB MP3; missing sidecar exits 1
with "run `newsdesk script` first").

- **Plugin (`morning/edgetts.py`):** registered as `TTS_ENGINES["edge-tts"]`
  by `load_plugins()`; input is the sidecar path, output is the report the
  publish stage consumes — `{date, mp3, duration_seconds, chunks, voices,
  sample_rate}` — plus a `<date>-audio.json` manifest sidecar (never
  published). Chunks are ≤2 sentences / ≤600 chars; pacing sleeps
  1.5–2.25 s (`NEWSDESK_TTS_PACE`, jittered) between requests.
- **Retries:** 4 retries + first try = 5 attempts per voice
  (`NEWSDESK_TTS_RETRIES`), exponential backoff 2/4/8/16 s + jitter capped at
  32 s. The catch is deliberately broad — edge-tts's failure modes (403,
  NoAudioReceived, websocket resets, timeouts) change exception shape across
  releases — but every failure is recorded in the manifest and reported if
  the chunk is finally lost.
- **Per-chunk voice fallback:** primary = the section's stamped voice
  (`settings.morning_voice`, stamped by the script-writer into the sidecar so
  it records what will be spoken; `NEWSDESK_MORNING_VOICE` configures it),
  alternate = `NEWSDESK_MORNING_VOICE_ALT`
  (en-US-EmmaMultilingualNeural). A chunk that exhausts the primary switches
  to the alternate for that chunk only.
- **Loud failure:** a chunk lost on every voice aborts with a clear TTSError
  → CLI exit 1, no MP3 written (final file is assembled via `.partial` +
  `os.replace`, so a failed render never leaves a half episode). Extra
  guards: every chunk must decode as MP3 with frames; chunks disagreeing on
  sample rate refuse to stitch; undecodable/empty output refuses to emit.
- **Duration is measured**, not estimated: an MP3 frame walk (ID3v2-skip,
  MPEG1/MPEG2/2.5 Layer III, re-sync on junk) sums frame durations — the
  number ticket 04's feed publishes.
- **Optional dep:** `newsdesk[tts]` extra (`edge-tts>=7.2,<8`), added to
  `all`; the network seam is the injectable `engine` callable, so unit tests
  run fully offline with a fake engine; `test_live_edge_tts_synthesis` does
  a real synthesis and skips when offline (run: `pytest -k live -rs`).
