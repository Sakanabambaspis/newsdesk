# Nightly Health & Security Audit — 2026-09-20

- **Run at:** 2026-09-20 21:00 (+0800) · completed well before the 22:15 deadline
- **Mode:** report-only — no project files modified, nothing committed or pushed
- **Workspace:** `/home/sakana/dev/personal/newsdesk`
- **Overrides:** `nightly/config.md` does not exist — no skips/ignores applied

## Layout discovered

| What | Found |
|---|---|
| Git repositories | 1 — `newsdesk` itself (`main`, HEAD `b1fd5cf`) |
| Package manifests | 1 — `pyproject.toml` (Python ≥ 3.11, hatchling) |
| Other manifests | none (`package.json`, `requirements*.txt`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle*` all absent) |

**Baseline note:** `nightly/archive/` is empty and no prior `audit.md`/`LATEST.md` exists — this is the **first** nightly report. "Changed since last night" has no baseline to diff against (see below).

---

## Project: newsdesk

### 1. Dependency vulnerability audit

- **Caveat found:** the repo has **no lockfile** (no `uv.lock`, no committed `requirements*.txt`), so the audit covers a fresh resolution of `pyproject.toml` (all extras: `all` + `dev`) rather than an installed/pinned set.
- **Method:** resolved 65 packages with `uv pip compile` into a temp dir (nothing written to the project), then scanned with **pip-audit** (run via `uvx`, ephemeral; PyPI advisory DB). Exit code 0.
- **Result: 0 known vulnerabilities** across all 65 resolved packages.
- *Recommendation:* commit `uv.lock` (or an exported requirements lock) so audits and CI check the exact set that runs, instead of whatever the resolver picks on audit day.

### 2. Outdated dependencies / major-version jumps

All 12 top-level dependencies resolve to the current PyPI latest — **nothing outdated, no major-version jumps**:

| Package | Resolved | PyPI latest | Note |
|---|---|---|---|
| httpx | 0.28.1 | 0.28.1 | up-to-date |
| feedparser | 6.0.14 | 6.0.14 | up-to-date |
| sqlmodel | 0.0.42 | 0.0.42 | up-to-date |
| typer | 0.27.2 | 0.27.2 | up-to-date |
| fastapi | 0.141.1 | 0.141.1 | up-to-date |
| uvicorn | 0.53.0 | 0.53.0 | up-to-date |
| pytest (dev) | 9.1.1 | 9.1.1 | up-to-date |
| yt-dlp (media) | 2026.8.19 | 2026.8.19 | up-to-date |
| imageio-ffmpeg (vision) | 0.6.0 | 0.6.0 | up-to-date |
| mcp | 2.2.0 | 2.2.0 | up-to-date |
| edge-tts | 7.2.8 | 7.2.8 | up-to-date (the `<8` cap is not currently binding) |
| keyring | 25.7.0 | 25.7.0 | up-to-date |

### 3. Secrets in the last 24 hours of commits

- **Scanned:** 23 commits (2026-09-20 04:10 → 12:45 +0800), added lines only, with file/line attribution.
- **Patterns:** PEM private-key blocks, AWS access keys (`AKIA…`), `api_key/secret/password/token/credential` assignments, GitHub/GitLab/Slack/OpenAI/Google token formats, credentials embedded in URLs, `Bearer` tokens.
- **Result: 0 hits.** The scan pipeline was positive-control tested against a synthetic diff to confirm it actually fires.
- Hygiene already in place: `.gitignore` excludes `.env` / `.env.*`.

### 4. TODO / FIXME / HACK markers

- **TODO: 0 · FIXME: 0 · HACK: 0** across 170 text files (code, `docs/`, `tests/`, `seed/`). Zero even case-insensitively — the codebase is marker-clean.

### 5. Repo hygiene

- **Merge-conflict markers:** none (`<<<<<<<` / `=======` / `>>>>>>>` scans clean).
- **Unusually large files:** none tracked — largest tracked file is 34.5 KB (`newsdesk/workflow/engine.py`). The only >1 MB files on disk are gitignored `.venv` compiled binaries (normal).
- **Uncommitted changes:** worktree fully clean — nothing staged, modified, or untracked.

---

## Changed since last night

**First report — no previous nightly exists, so there is no delta to report.** This report establishes the baseline; starting with the next run this section will list new/resolved vulnerabilities, new TODOs, and other changes versus the most recent prior report.

## Method notes & exclusions

- Marker/conflict scans exclude `.git/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `data/` (gitignored runtime data), and `nightly/` (this report's own output area).
- `nightly/` is not tracked in git (only the empty `archive/` dir existed, which git cannot track); today's files will show as untracked from the next audit onward — untracked by design, and not counted as repo dirt.
- Audit tooling ran from ephemeral/temp environments (`uv pip compile` → `/tmp`, `uvx pip-audit`); no project files were created, modified, or deleted, and nothing was committed or pushed.
