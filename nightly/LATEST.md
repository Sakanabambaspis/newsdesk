# Nightly Audit — LATEST

**2026-09-22** · full report: [`2026-09-22/audit.md`](2026-09-22/audit.md)

**Verdict: ✅ all clear, 2 benign watch items** — no vulnerabilities, no major-version jumps, no repo dirt; one new dummy test fixture in the secret scan, plus first-time visibility into venv drift (3 packages minor/patch behind latest).

| Check | Result |
|---|---|
| Vulnerabilities (pip-audit) | **0** — 60 installed pkgs audited in place, and 195-pkg fresh resolution, both clean |
| Outdated deps | **3 venv packages behind latest** (multidict, pydantic-core, sqlmodel — all minor/patch, **no major jumps**); manifest level fully current |
| Secrets in last-24h commits (2 commits) | **1 new flag** — `tests/test_episode_archive.py:14` dummy `TOKEN` fixture (value redacted; not a real credential); last night's `tests/test_agent_tools.py:27` still present, still benign |
| TODO / FIXME / HACK | **0 / 0 / 0** (3 prose hits in planning docs are not code markers) |
| Conflict markers · large tracked files · uncommitted changes | none · none (max 30.5 KB) · fully clean — `nightly/` is now tracked |

Watch items: (1) no lockfile is committed, so audits/CI can't check a pinned set — commit `uv.lock`; (2) the venv has drifted slightly from latest (`uv sync` would close it). Optional cleanup: rename the two dummy `TOKEN` fixtures or allowlist them so the nightly secret scan stays at zero.
