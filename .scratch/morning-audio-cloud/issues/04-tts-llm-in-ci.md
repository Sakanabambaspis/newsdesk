Type: research
Status: resolved
Blocked by:

## Question

Which TTS plugin and which LLM configuration can run unattended in CI,
within the everything-a-plugin architecture?

Sub-questions: edge-tts headless on ubuntu-latest — mechanism, output format,
current reliability (rate limits / 403 / NoAudioReceived) and mitigations
(pacing, retries), zh-CN + en-US voice quality, ToS gray area. Swappable
alternatives for future plugins: OpenAI TTS (cost), kokoro-onnx and Piper on
2-core runners (speed, Chinese support). LLM for the daily briefing:
user key as an Actions secret vs credible free tiers (Gemini/Groq/
OpenRouter/GitHub Models) vs the existing keyless extractive fallback;
secret-management basics.

Findings: `docs/research/tts-and-llm-in-ci.md`

## Answer

Both halves are viable unattended (evidence in
`docs/research/tts-and-llm-in-ci.md`, 220 lines, cited; includes a **live
smoke test run on this machine** — edge-tts synthesized en + zh headless in
<1s each, verified MP3 48kbps/24kHz mono).

- **TTS: edge-tts first plugin.** Pure Python, no key, no audio device.
  Maintenance is break-then-fix (Oct-2024 Sec-MS-GEC 403 wave; Dec-2025
  NoAudioReceived fix; intermittent server-side NoAudioReceived still open)
  → the plugin must chunk by paragraph, pace 1–3s between requests, retry
  4–5× with exponential backoff + per-chunk voice fallback, and fail loudly
  on missing chunks. Voices: zh-CN-XiaoxiaoNeural, en-US-AriaNeural, plus a
  multilingual voice for mixed scripts. ToS gray area acknowledged.
- **Swappable alternatives ranked:** OpenAI `gpt-4o-mini-tts` (~$0.15/run —
  the natural second plugin) > kokoro-onnx (English good, Chinese voices
  grade D) > Piper (repo archived, weak zh). All fit the CI time budget.
- **LLM: user key in an Actions secret as primary** (maps onto the existing
  `OpenAICompatAdapter`); automatic fallback to the existing keyless
  extractive path. Free tiers ranked: Groq (OpenAI-compatible, least adapter
  work), OpenRouter `:free` (50 req/day), Gemini free tier (viable volume
  but unpaid-tier content trains Google's models). **GitHub Models retired
  2026-07-30** — not an option.
- CI facts: secrets are masked and skipped on fork PRs (irrelevant to
  `schedule` triggers); private-repo runners are 2 vCPU/8GB; 360-min default
  timeout is ample.
