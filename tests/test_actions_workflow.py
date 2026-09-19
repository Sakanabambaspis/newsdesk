"""The Actions workflow is a contract (morning-audio-impl ticket 07).

Parsed as text — no YAML dependency. The assertions pin exactly what the
schedule decision (wayfinder tickets 01/06) and the credential split
(wayfinder ticket 09) settled: one cron at 07:07 HKT, manual dispatch for
backfilling and the failure probe, minimal permissions, credentials wired
by name only (secrets vs plain vars), and no credential material in the
file itself.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "morning.yml"


def _text() -> str:
    assert WORKFLOW.exists(), f"missing workflow file: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def _lines() -> list[str]:
    return _text().splitlines()


def _crons() -> list[str]:
    found = []
    for line in _lines():
        m = re.match(r"\s*-\s*cron:\s*[\"'](.+?)[\"']\s*(?:#.*)?$", line)
        if m:
            found.append(m.group(1))
    return found


def test_schedule_is_one_cron_at_the_accepted_time():
    # 23:07 UTC = 07:07 HKT; publish-by-08:30 deadline with runner-delay slack.
    assert _crons() == ["7 23 * * *"]


def test_dispatch_exists_for_backfill_and_failure_probe():
    text = _text()
    assert "workflow_dispatch:" in text
    for input_name in ("date", "fail"):
        assert re.search(rf"^\s+{input_name}:\s*$", text, re.MULTILINE), \
            f"missing dispatch input: {input_name}"


def test_minimal_permissions_and_serial_runs():
    text = _text()
    assert re.search(r"^permissions:\s*$", text, re.MULTILINE)
    assert re.search(r"^\s+contents:\s+read\s*$", text, re.MULTILINE)
    assert re.search(r"^concurrency:\s*$", text, re.MULTILINE)
    # overlapping schedule/dispatch runs queue instead of racing the deploy
    assert re.search(r"^\s+group:\s+morning\s*$", text, re.MULTILINE)


def test_job_is_unattended_ubuntu_with_a_time_bound():
    text = _text()
    assert "runs-on: ubuntu-latest" in text
    assert re.search(r"^\s*timeout-minutes:\s*\d+\s*$", text, re.MULTILINE)


def test_installs_audio_extra_and_runs_the_one_command():
    text = _text()
    assert "pip install .[tts]" in text, "the tts extra carries edge-tts"
    assert "newsdesk morning --json" in text
    # backfill passes the dispatch date through; env indirection only —
    # every "${{ inputs." line must be a MORNING_* env mapping, never a
    # script interpolation (injection-safe)
    assert '--date "$MORNING_DATE"' in text
    for line in _lines():
        if "${{ inputs." in line:
            assert re.match(r"\s+MORNING_\w+:\s", line), line


def test_seeds_the_fresh_runner_database_before_the_morning():
    # The runner DB starts empty; the committed seed file must be imported
    # before the pipeline runs, or the morning is a zero-source quiet day.
    lines = _lines()
    seed_idx = next((i for i, ln in enumerate(lines) if "newsdesk seed import" in ln), None)
    assert seed_idx is not None, "missing seed import step"
    morning_idx = next(i for i, ln in enumerate(lines) if "newsdesk morning --json" in ln)
    assert seed_idx < morning_idx, "seed must run before the morning"
    assert "seed/newsdesk-seed.json" in lines[seed_idx]


def test_credentials_wired_by_name_per_wayfinder_ticket_09():
    text = _text()
    assert "NEWSDESK_PUBLISHER: cloudflare-pages" in text
    secrets = ("NEWSDESK_FEED_TOKEN", "NEWSDESK_CLOUDFLARE_API_TOKEN",
               "NEWSDESK_LLM_API_KEY")
    plain_vars = ("NEWSDESK_FEED_BASE_URL", "NEWSDESK_CLOUDFLARE_ACCOUNT_ID",
                  "NEWSDESK_CLOUDFLARE_PROJECT", "NEWSDESK_LLM_BASE_URL",
                  "NEWSDESK_LLM_MODEL", "NEWSDESK_LLM_FALLBACK_MODELS")
    for name in secrets:
        assert ("${{ secrets." + name + " }}") in text, name
    for name in plain_vars:
        assert ("${{ vars." + name + " }}") in text, name


def test_llm_secret_is_optional_never_gated():
    # Missing key => NullAdapter => extractive episode still publishes. The
    # secret name may appear only on its env-mapping line (key + secrets ref),
    # so no step can require it or branch on it.
    lines = [ln for ln in _lines() if "NEWSDESK_LLM_API_KEY" in ln]
    assert len(lines) == 1
    assert re.match(r"\s+NEWSDESK_LLM_API_KEY:\s+\$\{\{ secrets\.\w+ \}\}\s*$",
                    lines[0]), lines[0]


def test_no_credential_material_in_the_file():
    text = _text()
    assert not re.search(r"sk-[A-Za-z0-9_-]{10,}", text)
    assert not re.search(r"\b[0-9a-f]{32}\b", text)
    assert not re.search(r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", text)
