# Config & environment audit — every knob the code actually reads

**Date:** 2026-09-17 (batch 2) · **Method:** grepped every `os.environ` / `os.getenv` read, every `settings.<field>` access, and every hardcoded module constant acting as a knob; compared against the README "Configuration" table, `docs/HARNESS.md`, and `docs/DESIGN.md` (§5.2, §13, §18). Report only; code untouched. Companion tests: `tests/test_config_settings.py`.

**Verdict:** the README configuration table is accurate as far as it goes, but it is incomplete: **1 env var read at runtime is documented nowhere** (`NEWSDESK_REQUEST_TIMEOUT`), **2 OpenAI-compatible fallback vars are undocumented**, and **~8 non-env knobs/quirks** (retry counts, per-kind caps, LLM timeouts not wired to the request timeout, one docstring that documents the wrong precedence) are invisible to a reader of the docs. No dead Settings fields exist — every field is read somewhere.

---

## A. Environment variables read at runtime

All reads happen in `newsdesk/config.py::Settings.from_env` and `newsdesk/accounts/credentials.py::_env_names` — the only two places env is consulted. "Documented" = appears in README config table / HARNESS.md / DESIGN.md.

| Env var | Where read | Default | Documented? |
|---|---|---|---|
| `NEWSDESK_HOME` | config.py:51 | `~/.newsdesk` | README ✓ HARNESS ✓ DESIGN ✓ |
| `NEWSDESK_DB_URL` | config.py:54 | SQLite under home | README ✓ DESIGN §13 ✓ HARNESS ✗ |
| `NEWSDESK_USER_AGENT` | config.py:55 | `NewsdeskBot/0.1 (local research agent; respects robots.txt)` | README ✓ DESIGN §5.2 ✓ |
| `NEWSDESK_REQUEST_TIMEOUT` | config.py:56 | `20.0` s | **✗ undocumented everywhere** |
| `NEWSDESK_MIN_INTERVAL` | config.py:57 | `5.0` s per host | README ✓ DESIGN §5.2 ✓ |
| `NEWSDESK_LLM_BASE_URL` | config.py:58-60 | None → `OPENAI_BASE_URL` fallback | README ✓ (fallback **✗**) HARNESS ✓ |
| `NEWSDESK_LLM_API_KEY` | config.py:61-63 | None → `OPENAI_API_KEY` fallback | README ✓ (fallback **✗**) HARNESS ✓ |
| `NEWSDESK_LLM_MODEL` | config.py:64 | `gpt-4o-mini` | README ✓ |
| `NEWSDESK_LLM_AUDIO_MODEL` | config.py:65 | `whisper-1` | README ✓ |
| `NEWSDESK_VISION_MODEL` | config.py:66 | None → code falls back to `llm_model` (vision.py:65), ultimate fallback `gpt-4o-mini` | README ✓ (final fallback unstated) |
| `NEWSDESK_TRANSCRIBE_AUDIO` | config.py:67-68 | false; truthy set is exactly `1/true/yes` (case-insensitive) | README ✓ (accepted values unstated) |
| `NEWSDESK_KEYFRAME_INTERVAL` | config.py:69 | `30.0` s | README ✓ |
| `NEWSDESK_ACCOUNT__<KIND>__<REF>__<FIELD>` | credentials.py:37 | — (per-account credentials) | README ✓ (email shape) DESIGN §18 ✓ |
| `NEWSDESK_EMAIL_HOST/PORT/USER/PASSWORD` | credentials.py:38-39 (short shape, `ref=default` only) | — | README ✓ DESIGN §18 ✓ |
| `NEWSDESK_TWITTER_ACCESS_TOKEN` | via fields table (`manager.PROVIDERS`) | — | README ✓ DESIGN §18 ✓ |
| `NEWSDESK_YOUTUBE_API_KEY` / `NEWSDESK_YOUTUBE_CHANNEL_ID` | via `_ENV_KIND` alias (`youtube-account`→`youtube`) | — | README ✓ DESIGN §18 ✓ |

Notes:
- `OPENAI_BASE_URL` / `OPENAI_API_KEY` are read as fallbacks when the `NEWSDESK_LLM_*` vars are unset; `NEWSDESK_LLM_*` wins when both are set (pinned by `tests/test_config_settings.py::test_openai_env_fallbacks`).
- The `_ENV_KIND` alias means `youtube-account` credentials use the friendlier `NEWSDESK_YOUTUBE_*` prefix while the keyring service keeps the full `newsdesk/youtube-account/<ref>` name (documented in `credentials.py` docstring ✓).

## B. Settings fields with no env wiring (code-only knobs)

| Field | Default | Where read | Documented? |
|---|---|---|---|
| `max_retries` | 2 | fetcher.py:91 (retry loop: attempts = max_retries+1) | DESIGN §5.2 says "bounded retries" but no way to change it; **not env-wired, not in README** |
| `max_items_per_feed` | 200 | rss.py:98, video.py:113 (further capped by `VIDEO_DETAIL_CAP=25`), arxiv.py:57,93 (capped at 100), email.py:128, twitter.py:105 | **not env-wired, undocumented**; the per-kind sub-caps interplay is unstated |

Every other `Settings` field is env-wired or derived (`snapshots_dir`, `media_dir`, `database_url`, `ensure_dirs()`). No dead fields.

## C. Hardcoded constants that act as knobs (undocumented)

| Constant | Value | Location | Effect |
|---|---|---|---|
| `VIDEO_DETAIL_CAP` | 25 | ingest/video.py:32 | per-run per-channel video detail fetches (overrides `max_items_per_feed`) |
| `SNAPSHOT_TRANSCRIPT_LIMIT` | 200 000 chars | ingest/video.py:33 | transcript kept per video in snapshots |
| `BODY_LIMIT` | 200 000 chars | accounts/email.py:29 | message body kept per email item |
| `SNAPSHOT_LIMIT` | 500 000 chars | accounts/email.py:30 | raw RFC822 kept per message in snapshots |
| `SNIPPET_CHARS` / `MAX_LLM_ITEMS` / `MAX_THEMES` | 400 / 30 / 8 | pipeline/digest.py:20-22 | digest brief size, LLM window, theme count |
| `MAX_FRAMES` / `FRAME_WIDTH` | 8 / 768 px | media/vision.py:21-22 | keyframes per vision request |
| twitter `max_results` | 100 (API max) | accounts/twitter.py:69 | per-run timeline page |
| arXiv `max_results` | min(100, max_items_per_feed) | ingest/arxiv.py:57 | per-run per-category page |
| YouTube subscription page/cap | 50 / 500 | accounts/youtube_account.py:43,49 | subscription sync pages |

## D. Doc-vs-code mismatches (worth fixing in docs)

1. **`NEWSDESK_REQUEST_TIMEOUT` is a live knob with zero documentation** (README table, HARNESS.md, DESIGN.md all omit it), and its reach is narrower than the name suggests: it sizes the HTTP fetch client (fetcher.py:31) but **not** the LLM calls — chat completions hardcode 60 s (openai_compat.py:17) and vision/audio floor at 120 s (`max(settings.request_timeout, 120.0)`, transcripts.py:100, vision.py:87). A user raising the timeout to fix a slow LLM endpoint would see no effect.
2. **Credential precedence documented backwards**: `accounts/credentials.py:1` says "OS keyring first, environment fallback", but `resolve()` (credentials.py:49-57) checks **environment first**, keyring second. README's "env or the OS keyring" is agnostic but the module docstring is wrong.
3. **`vision_model` fallback chain**: README's "`= NEWSDESK_LLM_MODEL`" is right, but when *neither* is set the code's ultimate fallback is `gpt-4o-mini` (vision.py:65) — an OpenAI-specific model name hardcoded as the last resort for a vendor-neutral adapter.
4. **`NEWSDESK_TRANSCRIBE_AUDIO` accepted values** (`1`, `true`, `yes`, case-insensitive; anything else, including `on`, is false) are unpinned in docs — pinned by `tests/test_config_settings.py::test_transcribe_audio_truthy_parse`.

## E. Config *files* (non-env)

| File | Read by | Shape | Documented? |
|---|---|---|---|
| `$NEWSDESK_HOME/accounts.json` | accounts/manager.py (`GRANTS_FILENAME`) | `{kind/ref: {capabilities: [...], granted_at}}`; corrupted JSON silently reads as `{}` (manager.py:52-55) | DESIGN §18 ✓ (rules 1-2); silent-corruption behavior undocumented |
| OS keyring service `newsdesk/<kind>/<ref>` | credentials.py (only when the `keyring` extra is importable) | one item per field (username = field name) | credentials.py docstring ✓, README (keyring extra) ✓; precedence mismatch → D2 |

## F. HARNESS.md cross-check

- "inherits `NEWSDESK_HOME` and `NEWSDESK_LLM_*` from the environment" — **true**: `mcp_server._settings()` / `_run()` rebuild `Settings.from_env()` per tool call (note: per-call, so env changes mid-session apply to the next call; audit D6).
- "No credentials are stored by the MCP server itself; it only reads `NEWSDESK_*` env vars" — **true** (no keyring writes anywhere; `resolve()`/`presence()` only read).

## Flag count

**3 undocumented env-level knobs** (A: `NEWSDESK_REQUEST_TIMEOUT`, `OPENAI_BASE_URL` fallback, `OPENAI_API_KEY` fallback) + **8 undocumented/quirk findings** (B: 2 unwired fields; C: 9 constants in 1 group; D: 4 mismatches, one duplicated in C) → **11 flagged items**, no dead knobs.

---

## Postscript — overnight run incident (observed, not fixed)

During this batch's final full-suite run, the host's `keyring` backend began **blocking at `import keyring`** (DB us secret-service autolaunch with no reachable bus; a bare `python -c "import keyring; keyring.get_keyring()"` never returns). This wedged any code path touching `credentials._keyring()` — including the pre-existing `tests/test_accounts.py` — even though the same suite passed minutes earlier. The behavior validates the audit's D13 note that `_keyring()` catches *exceptions* but cannot defend against a *blocking* import. The suite was verified green (160 passed, 7.58 s) by making the optional import fail fast (shim plugin outside the repo, `sys.modules["keyring"] = None`), which is exactly the code's designed degradation when the `accounts` extra is absent. Suggested hardening, for a future ticket: run the keyring probe in a subprocess with a timeout, or document `keyring=/dev/null`-style opt-out env knob.

