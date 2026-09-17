# TTS and LLM options for the daily CI pipeline

Researched 2026-09-18 for the scheduled daily GitHub Action that (1) renders the
digest script (800-1500 words, zh or en) to MP3 and (2) generates the briefing
text. Both are swappable adapters in newsdesk's plugin design. Method: primary
sources (project repos, PyPI, official docs), plus a live headless-Linux smoke
test of edge-tts on the research machine. Items marked **[unverified]** could
not be confirmed against a primary source from this environment.

## 1. edge-tts (chosen first TTS plugin)

1.1 **What it is.** `edge-tts` is a Python package (PyPI 7.2.8, released
2026-03-22, LGPLv3 except `srt_composer.py` MIT) that uses "Microsoft Edge's
online text-to-speech service from within your Python code" with "WITHOUT
needing Microsoft Edge or Windows or an API key" per the README.
Sources: https://pypi.org/project/edge-tts/ , https://github.com/rany2/edge-tts

1.2 **Mechanism.** It opens a WebSocket to
`wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=...`
and sends a `Sec-MS-GEC` DRM token derived from a spoofed Chromium version
(currently 143.0.3650.75) and an Edge-extension `Origin` header. Default voice
is `en-US-EmmaMultilingualNeural`. Output is the classic Azure format
`audio-24khz-48kbitrate-mono-mp3` (`MP3_BITRATE_BPS = 48_000` in constants.py).
Source: https://github.com/rany2/edge-tts/blob/master/src/edge_tts/constants.py

1.3 **Python API.** Two lines per file:
`communicate = edge_tts.Communicate(TEXT, VOICE)` then `await communicate.save(path)`
(or `save_sync` for blocking code; `stream()` yields audio+subtitle chunks).
CLI equivalent: `edge-tts --voice X --text "..." --write-media out.mp3`.
Rate/volume/pitch via `--rate=-10%`-style args; custom SSML was removed because
Microsoft only allows what Edge itself could send.
Sources: examples dir (sync_audio_gen_with_predefined_voice.py), PyPI page.

1.4 **Headless CI: verified live.** Pure Python (aiohttp-based), no browser, no
audio device, no key — writing to file needs nothing else. On 2026-09-18 this
research machine (headless Linux) ran edge-tts 7.2.8 end to end: `--list-voices`
worked, and `en-US-AriaNeural` + `zh-CN-XiaoxiaoNeural` samples synthesized in
<1 s each; `file(1)` confirmed both outputs are "MPEG ADTS layer III, 48 kbps,
24 kHz, Monaural". Nothing about the workload needs a display or sound card, so
ubuntu-latest is fine.

1.5 **zh + en voices.** Verified live from `--list-voices` (7.2.8):
`zh-CN-XiaoxiaoNeural` (Female, "News, Novel", Warm), `zh-CN-YunyangNeural`
(Male, News, "Professional, Reliable"), `zh-CN-YunxiNeural`, plus `en-US-AriaNeural`
(Female, "News, Novel") and the Multilingual voices (Andrew/Emma). Microsoft's
catalog confirms Xiaoxiao has a `newscast` style and Yunyang `narration-professional`;
standard Neural voices run at 24 kHz.
Source: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support

1.6 **Maintenance.** Healthy-but-reactive: releases on 2025-08-05/20/28,
2025-12-11/12, 2026-03-22 (7.2.8, latest; repo pushed same day; only 4 open
issues). Notable notes: 7.2.2 "Update API endpoints used by Edge TTS",
7.2.4 "Resolve NoAudioReceived issue" (2025-12-11), 7.1.0 fixed "audio cutting
off at 10:00". Expect breakage-then-fix cycles, not steady APIs.
Source: https://github.com/rany2/edge-tts/releases

1.7 **Reliability history.**
- Oct 2024: Microsoft added the `Sec-MS-GEC` token; handshake 403s broke every
  client until PR #303 implemented it (issue #290 "403 error is back/need to
  implement Sec-MS-GEC token"). Earlier 403 waves in issues #265/#286/#287/#288.
  Source: https://github.com/rany2/edge-tts/issues/290
- Dec 2025: 7.2.4 explicitly "Resolve NoAudioReceived issue".
- Ongoing: #473 (open, 2026-04) "Intermittent 'No audio was received' errors
  with valid requests" — single-threaded, valid params, no maintainer reply;
  #481 (open, 2026-07) NoAudioReceived localized to Armenian/Punjabi/Basque
  voices; #437 "not working in linux machines" (closed) was NoAudioReceived on
  Ubuntu. These are server-side, not client bugs.
- No published rate-limit numbers exist; the project is unofficial with no SLA.

1.8 **ToS gray area (stated honestly).** This is an undocumented consumer
endpoint, not a public Microsoft API. Microsoft has actively raised the bar
(DRM token in 2024, endpoint changes in 2025) and could block or terminate
access at any time; nothing in the docs authorizes programmatic use. Fine for a
personal project with one run per day; do not build anything load-bearing on it.
Keep a paid TTS as the swap-in plan.

## 2. Swappable TTS alternatives (future plugins)

2.1 **OpenAI TTS API** (paid, most robust). Current model `gpt-4o-mini-tts`
(promptable voices, streaming, mp3/opus/aac/flac/wav/pcm output): priced as
$0.60/1M text input tokens + $12/1M audio output tokens ≈ **$0.015 per minute
of audio** (~$15/1M chars effective). Legacy `tts-1` $15/1M chars,
`tts-1-hd` $30/1M chars. Sources:
https://developers.openai.com/api/docs/models/gpt-4o-mini-tts ,
https://azure.microsoft.com/en-us/pricing/details/azure-openai/ ,
https://community.openai.com/t/how-do-i-calculate-the-usage-cost-when-using-the-gpt-4o-mini-tts-model/1263804
**[unverified detail: 4096-char input cap per request — recheck at build time.]**
Our job (≤1500 words ≈ ≤10 min audio) ≈ **$0.15/run, ~$55/yr**. Trivial cost,
SLA-backed, zero breakage risk. Supports zh and en well.

2.2 **kokoro-onnx** (local, offline). Runs the 82M-param Kokoro model
(Apache 2.0, StyleTTS2-based, 24 kHz) via onnxruntime; model ~300 MB or ~80 MB
int8. Repo claims "fast performance near real-time on macOS M1"; no official
x86 benchmark — **[estimate: RTF ~0.2-0.5 on a 2-4 core runner, so ~10 min of
audio in ~2-5 min CPU; safe within 6 h]**. Quality is strong for English
(`af_heart` graded A in the author's VOICES.md) but **Chinese is weak**: all 8
zh voices (`zf_xiaobei` … `zm_yunyang`) grade **Overall D**, with only
minutes-to-low-hours of zh training data and an explicit warning that non-English
support "may be absent or thin". Sources:
https://github.com/thewh1teagle/kokoro-onnx ,
https://huggingface.co/hexgrad/Kokoro-82M ,
https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md

2.3 **Piper** (local, older). Original rhasspy/piper was **archived 2025-10-06**;
development moved to OHF-Voice/piper1-gpl (GPL-3.0, embeds espeak-ng). ~60
languages on HuggingFace (rhasspy/piper-voices) including zh_CN voices
`huayan`, `chaowen`, `xiao_ya`; design goal "optimized for the Raspberry Pi 4",
so a CI runner is no problem for 10 min of audio. But zh voices are few,
2023-era VITS quality, and the project is in a maintenance transition.
Sources: https://github.com/rhasspy/piper , https://github.com/OHF-Voice/piper1-gpl ,
https://huggingface.co/rhasspy/piper-voices

2.4 **Ranking for a 6-hour, 2-core CI job** (all four fit the time budget easily;
the 6 h cap and 2-core spec are per GitHub docs, see 4.2):
1. **edge-tts** — free, best zh+en quality; risk is silent upstream breakage.
2. **OpenAI TTS** — ~$0.15/run, robust, would be the paid fallback plugin.
3. **kokoro-onnx** — offline safety net; fine for en, poor for zh.
4. **Piper** — last resort; zh quality/availability weakest, project in flux.

## 3. LLM for the daily briefing in an unattended Action

3.1 **Own key via Actions secret (primary path).** Secrets reach the job as
masked env vars (see 4.1), so mapping
`NEWSDESK_LLM_BASE_URL/NEWSDESK_LLM_API_KEY/NEWSDESK_LLM_MODEL` from
`${{ secrets.* }}` activates newsdesk's existing `OpenAICompatAdapter`
(`get_adapter` in newsdesk/llm/base.py uses it whenever base_url+key are set;
any OpenAI-compatible chat endpoint works).

3.2 **Free tiers for a once-daily ~4k-token call:**
- **Google Gemini** free tier: exists (AI Studio API key). Docs no longer
  publish static RPM/RPD numbers — they defer to the per-project AI Studio
  dashboard ("Rate limits … can be viewed in Google AI Studio"; RPD resets
  midnight Pacific). **[unverified: the long-reported ~10 RPM/250 RPD for
  2.5-flash — check your own dashboard.]** Data-use caveat (verbatim from the
  Gemini API Additional Terms): "When you use Unpaid Services, including … the
  unpaid quota on Gemini API, Google uses the content you submit to the
  Services and any generated responses to provide, improve, and develop Google
  products and services and machine learning technologies." Your collected news
  text would train Google's models. Sources:
  https://ai.google.dev/gemini-api/docs/rate-limits , https://ai.google.dev/gemini-api/terms
- **Groq** free tier: OpenAI-compatible API (fits `OpenAICompatAdapter`
  unchanged); per-model RPM/TPD limits shown in the console dashboard.
  **[unverified: console.groq.com blocked all fetches from this environment —
  exact free-tier numbers not confirmable here.]**
  Source: https://console.groq.com/docs/rate-limits
- **OpenRouter** `:free` models: 20 requests/min; **50 free-model requests/day**
  with <$10 lifetime credit purchases, **1000/day** at ≥$10 (effectively ≥9).
  OpenAI-compatible; free-model roster churns. Source:
  https://openrouter.ai/docs/api-reference/limits
- **GitHub Models: do not use — fully retired as of 2026-07-30** ("The
  playground, model catalog, inference API, and bring your own key (BYOK) are
  no longer available"). Source:
  https://docs.github.com/en/github-models/use-github-models/prototyping-with-ai-models

3.3 **Is free tier viable long-term?** By volume, yes — one ~4k-token call/day
(~120/yr) is far under any of the above limits. The real risks are roster/model
churn and silent unattended failure, not quotas. A $5-scale paid key or a
free-tier key you already own is more durable than any :free model.

3.4 **Zero-key path already exists.** With no LLM configured, newsdesk's
digest pipeline calls `_fallback_digest`, producing a clearly-labeled extractive
briefing (`"method": "extractive"`, "set an LLM key for an LLM-written briefing")
from the pre-ranked items — dumber, but fully automatic and grounded in item IDs.
Sources: newsdesk/pipeline/digest.py, newsdesk/cli.py ("grounded summary
(extractive without an LLM)").

## 4. GitHub Actions basics relevant here

4.1 **Secrets.** Repo/org/environment scopes; consumed via the `secrets`
context, typically into env vars; GitHub "redacts registered secrets in
workflow logs"; unset secrets evaluate to empty string; 48 KB per-secret cap.
Gotchas: org secrets "are not accessible by private repositories for GitHub
Free"; and "secrets are not passed to the runner when a workflow is triggered
from a forked repository" (also Dependabot events). For scheduled runs from your
own repo the fork restriction is irrelevant — `schedule` events on your repo get
secrets normally. Source:
https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions

4.2 **Runner size and time.** Standard ubuntu runners: **4 vCPU / 16 GB / 14 GB
on public repos, 2 vCPU / 8 GB / 14 GB on private repos**; `jobs.<job_id>.timeout-minutes`
**default 360** (6 h), which is also the max on GitHub-hosted runners.
Sources: https://docs.github.com/en/actions/reference/github-hosted-runners-reference ,
`jobs.<job_id>.timeout-minutes` in
https://github.com/github/docs/blob/main/content/actions/reference/workflows-and-actions/workflow-syntax.md

## Implications for newsdesk

**TTS plugin defaults (edge-tts):**
- zh digest: `zh-CN-XiaoxiaoNeural` (female, newscast style); alternate
  `zh-CN-YunyangNeural` (male, narration-professional). en digest:
  `en-US-AriaNeural`. Mixed zh+en in one file: use a Multilingual voice
  (`en-US-EmmaMultilingualNeural`, the edge-tts default) or pick the voice per
  chunk by script detection — multilingual voices auto-detect language.
- Chunking: split the script into ~1-2 sentence paragraphs (well under any
  length limit; also avoids the old 10:00-cutoff class of bugs).
- Pacing: sleep 1-3 s between chunk requests. A 10-min digest is ~10-20 chunks
  → ~1-2 min wall time, far inside 6 h and indistinguishable from light
  interactive use.
- Retry policy: per chunk, retry up to 4-5x with exponential backoff + jitter
  (e.g. 2 s → 32 s) on `NoAudioReceived`, timeouts, and 403s; on a still-failing
  chunk try the alternate voice for that language before giving up. Fail the
  job loudly if any chunk never renders — never ship silently truncated audio.
  Sanity-check output (file exists, non-trivial size, decodes as 24 kHz MP3).
- Pin `edge-tts` in requirements but expect periodic bumps for upstream fixes
  (1.6); treat the whole plugin as swappable (2.4) because the endpoint is a
  ToS gray area (1.8).

**LLM config for unattended runs:**
- Primary: user's OpenAI-compatible key in an Actions secret, mapped to
  `NEWSDESK_LLM_BASE_URL` / `NEWSDESK_LLM_API_KEY` / `NEWSDESK_LLM_MODEL` job
  env → existing `OpenAICompatAdapter` just works (3.1).
- Fallback: leave the existing behavior in place — if the secret is absent or
  the call fails, `_fallback_digest` produces the labeled extractive briefing
  (3.4). No code change needed for the zero-key case.
- If no paid key: Groq free tier is the least-friction option (OpenAI-compatible
  base URL, drop-in), Gemini free tier also viable but note the unpaid-tier
  training-use policy (3.2); OpenRouter `:free` at 50 req/day only if you keep
  ≥$9-10 in credits for the higher cap. GitHub Models is retired — excluded.
  Volume is a non-issue for any of them; plan for model-name churn via config,
  and let the extractive fallback absorb outages.
