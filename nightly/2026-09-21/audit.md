# Nightly Health & Security Audit — 2026-09-21

- **Run at:** 2026-09-21 21:04 (+0800) · completed well before the 22:15 deadline
- **Mode:** report-only — no project files modified, nothing committed or pushed
- **Workspace:** `/home/sakana/dev/personal/newsdesk`
- **Overrides:** `nightly/config.md` does not exist — no skips/ignores applied

## Layout discovered

| What | Found |
|---|---|
| Git repositories | 1 — `newsdesk` itself (`main`, HEAD `10f2096`) |
| Package manifests | 1 — `pyproject.toml` (Python ≥ 3.11, hatchling) |
| Other manifests | none (`package.json`, `requirements*.txt`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle*` all absent) |

---

## Project: newsdesk

### 1. Dependency vulnerability audit

- **Caveat (unchanged):** the repo still has **no lockfile** (no `uv.lock`, no committed `requirements*.txt`), so the audit covers a fresh resolution of `pyproject.toml` (all extras: `all` + `dev`) rather than an installed/pinned set.
- **Method:** resolved the dependency tree with `uv pip compile` into a temp dir (nothing written to the project) — **66 packages** — then scanned with **pip-audit** (run via `uvx`, ephemeral; PyPI advisory DB). Exit code 0.
- **Result: 0 known vulnerabilities** across all 66 resolved packages.
- *Standing recommendation:* commit `uv.lock` (or an exported requirements lock) so audits and CI check the exact set that runs.

### 2. Outdated dependencies / major-version jumps

All 12 top-level dependencies resolve to the current PyPI latest — **nothing outdated, no major-version jumps**. Every version is identical to last night's report:

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

- **Scanned:** 2 commits (`6060ce1` impl workflow-14 W5 agent tools, `10f2096` docs ticket 14; 2026-09-21 03:48 +0800), added lines only, with file/line attribution.
- **Patterns:** PEM private-key blocks, AWS access keys (`AKIA…`), `api_key/secret/password/token/credential` assignments, GitHub/GitLab/Slack/OpenAI/Google token formats, credentials embedded in URLs, `Bearer` tokens. Scanner positive-control tested against a synthetic diff before the real run (3/3 synthetic hits fired).
- **Result: 1 flag — benign test fixture, value redacted:**
  - `tests/test_agent_tools.py:27` — module-level constant `TOKEN = "…"` added in `6060ce1`. It is a dummy fixture feeding mocked agent-tool calls in the test file, not a credential. Flagged for completeness; no action strictly required. If the team prefers, rename (e.g. `FAKE_TOKEN`) or add a scanner allowlist marker so nightly reports stay clean.

### 4. TODO / FIXME / HACK markers

- **TODO: 0 · FIXME: 0 · HACK: 0** across 180 tracked files (code, `docs/`, `tests/`, `seed/`). Zero even case-insensitively.

### 5. Repo hygiene

- **Merge-conflict markers:** none (`<<<<<<<` / `=======` / `>>>>>>>` scans clean).
- **Unusually large files:** none tracked — largest tracked file is 30.5 KB (`newsdesk/workflow/engine.py`). No files > 1 MB on disk outside `.venv/`/`.git/`.
- **Uncommitted changes:** worktree clean; the only untracked path is `nightly/` — this report's own output area, untracked by design.
- `.env` exists on disk at the repo root (local config) but is **gitignored and untracked** — correct handling.

---

## Changed since last night

Baseline: [`2026-09-20/audit.md`](../2026-09-20/audit.md). Two commits landed in the window (`6060ce1`, `10f2096` — the workflow-14 W5 agent-tools work).

| Area | Last night | Tonight | Δ |
|---|---|---|---|
| Known vulnerabilities | 0 (65 pkgs) | 0 (66 pkgs) | none new, none resolved |
| Outdated deps / major jumps | none | none | unchanged |
| Secret flags in new commits | 0 | 1 (benign test fixture, see §3) | **+1 new flag** |
| TODO / FIXME / HACK | 0 / 0 / 0 | 0 / 0 / 0 | unchanged |
| Conflict markers | none | none | unchanged |
| Worktree | fully clean | clean except untracked `nightly/` | expected (report output) |

- **New since last night:** the one secret-scan flag above (`tests/test_agent_tools.py:27`, dummy fixture). Plus ~2,000 new lines of agent-tools surface (`newsdesk/agents/tools.py`, `newsdesk/api/app.py`, `newsdesk/workflow/bindings.py`, expanded `mcp_server.py` and tests) — noted for context, nothing adverse.
- **Resolved since last night:** nothing to resolve — there were no open vulnerabilities or TODO markers in the baseline.

## Method notes & exclusions

- Marker/conflict scans cover git-tracked files only; they exclude `.git/`, `.venv/`, `__pycache__/`, caches, gitignored runtime data, and `nightly/`.
- Audit tooling ran from ephemeral/temp environments (`uv pip compile` → `/tmp/nightly-audit-2026-09-21`, `uvx pip-audit`); no project files were created, modified, or deleted, and nothing was committed or pushed.
