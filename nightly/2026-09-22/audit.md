# Nightly Health & Security Audit — 2026-09-22

- **Run at:** 2026-09-22 21:00–21:06 (+0800) · completed well before the 22:15 deadline
- **Mode:** report-only — no project files modified, nothing committed or pushed
- **Workspace:** `/home/sakana/dev/personal/newsdesk`
- **Overrides:** `nightly/config.md` does not exist — no skips/ignores applied

## Layout discovered

| What | Found |
|---|---|
| Git repositories | 1 — `newsdesk` itself (`main`, HEAD `0ef9877`) |
| Package manifests | 1 — `pyproject.toml` (Python ≥ 3.11, hatchling) |
| Other manifests | none (`package.json`, `requirements*.txt`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle*` all absent) |

---

## Project: newsdesk

### 1. Dependency vulnerability audit

- **Primary (installed set):** audited the project `.venv` in place — **60 installed packages** — with **pip-audit** (run via `uv tool run`, ephemeral environment; PyPI advisory DB). **Result: 0 known vulnerabilities.** The only skip is `newsdesk` itself (local package, not on PyPI) — expected.
- **Comparability with prior nights:** also resolved `pyproject.toml` fresh (all extras: `all` + `dev`) into `/tmp` via `uv pip compile` — **195 packages** — and audited that set too: **0 known vulnerabilities**.
- **Standing caveat (unchanged):** the repo still has **no lockfile** (no `uv.lock`, no committed `requirements*.txt`), so there is no pinned set for CI or audits to check against. Committing `uv.lock` remains the recommendation.

### 2. Outdated dependencies / major-version jumps

- **Venv drift (installed vs PyPI latest): 3 of 60 packages behind — no major-version jumps** (all within-major bumps):

| Package | Installed | PyPI latest | Jump |
|---|---|---|---|
| multidict | 6.9.0 | 6.9.1 | patch — no |
| pydantic-core | 2.46.5 | 2.49.0 | minor (2.x) — no |
| sqlmodel | 0.0.45 | 0.0.46 | patch (0.0.x line) — no |

- **Manifest level:** a fresh resolution of `pyproject.toml` picks the current latest for all 12 top-level dependencies (same as last night); nothing outdated there. The `edge-tts <8` cap is still not binding.
- First time this audit has looked at the *installed* set rather than a fresh resolution — the three entries above are venv drift, not manifest drift. A `uv sync` (or lockfile) would close it.

### 3. Secrets in the last 24 hours of commits

- **Scanned:** 2 commits (`626d64e` 2026-09-22 17:28 morning-archive feature + program docs; `0ef9877` 17:36 briefing archive backfill), added lines only, with file/line attribution. Values redacted.
- **Patterns:** PEM private-key blocks, AWS access keys (`AKIA…`), `api_key/secret/password/token/credential` assignments, GitHub/GitLab/Slack/OpenAI/Google token formats, credentials in URLs, `Bearer` tokens.
- **Result: 1 flag — benign test fixture, value redacted:**
  - `tests/test_episode_archive.py:14` — module-level constant `TOKEN = "…"` (32-char hex, comment: "128-bit hex, like production"), added in `626d64e`. Dummy fixture feeding a mocked-archiving test, not a credential — same class as last night's `tests/test_agent_tools.py:27` (still present, still benign).
- **Also verified clean:** the two briefing JSON files committed in `0ef9877` (`briefing/morning-briefing/2026-09-18/meta.json`, `script.json`) contain no token-like strings; `newsdesk/morning/archive.py` reads the feed token from settings and actively raises if a bundle would carry it.

### 4. TODO / FIXME / HACK markers

- **TODO: 0 · FIXME: 0 · HACK: 0** as code markers, across all 193 tracked files (case-insensitive scan).
- 3 case-insensitive hits are all prose, not markers — the phrase "single source of todo" in newly tracked planning docs: `AGENTS.md:5`, `docs/agents/issue-tracker.md:13`, `.scratch/program/map.md:8`.

### 5. Repo hygiene

- **Merge-conflict markers:** none (`<<<<<<<` / `=======` / `>>>>>>>` scans clean).
- **Unusually large files:** largest tracked file unchanged at 30.5 KB (`newsdesk/workflow/engine.py`); no files > 1 MB on disk outside `.venv/`/`.git/`.
- **Uncommitted changes:** worktree **fully clean** — nothing modified, nothing staged, nothing untracked. Note the change: `nightly/` is now **tracked** (committed in `626d64e`), whereas last night it was the only untracked path.
- `.env` exists on disk at the repo root but is **gitignored and untracked** — correct handling.

---

## Changed since last night

Baseline: [`2026-09-21/audit.md`](../2026-09-21/audit.md). Two commits landed in the window (`626d64e`, `0ef9877`, +938 lines): the morning-episode-archive feature, the program planning docs (`AGENTS.md` routing, `.scratch/program/`, `docs/agents/issue-tracker.md`), the 2026-09-18 briefing archive, and the nightly reports themselves becoming tracked.

| Area | Last night | Tonight | Δ |
|---|---|---|---|
| Known vulnerabilities | 0 (66 resolved pkgs) | 0 (60 installed + 195 resolved) | none new, none resolved |
| Outdated deps / major jumps | none | 3 venv packages behind latest (all minor/patch) | **+3 (venv drift, no major jumps)** |
| Secret flags in new commits | 1 (benign fixture `test_agent_tools.py:27`) | 1 new (benign fixture `test_episode_archive.py:14`) | **+1 new flag** (old one unresolved) |
| TODO / FIXME / HACK | 0 / 0 / 0 | 0 / 0 / 0 | unchanged |
| Conflict markers | none | none | unchanged |
| Worktree | clean except untracked `nightly/` | fully clean (`nightly/` now tracked) | improved |
| Tracked files | 180 | 193 | +13 (planning docs, briefing archive, nightly reports) |

- **New since last night:** the benign `tests/test_episode_archive.py:14` fixture flag; venv drift on multidict / pydantic-core / sqlmodel; +13 tracked files (nothing adverse).
- **Resolved since last night:** nothing to resolve — no open vulnerabilities or TODO markers existed in the baseline.

## Method notes & exclusions

- Marker/conflict scans cover git-tracked files only; they exclude `.git/`, `.venv/`, `__pycache__/`, caches, gitignored runtime data, and `nightly/` report content.
- The venv audit used `pip-audit --path` against `.venv/lib/python3.13/site-packages` from an ephemeral `uv tool run` environment; the resolution audit used `/tmp/nightly-audit-2026-09-22`. Nothing was installed into the project venv, no project files were created, modified, or deleted, and nothing was committed or pushed.
