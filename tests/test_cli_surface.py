"""Characterization tests for CLI surface paths not covered by
test_cli.py / test_accounts tests: error exits, the daily-briefing command,
and the accounts command family. Offline, in-process via CliRunner.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from newsdesk.cli import app

runner = CliRunner()


def _home(tmp_path, monkeypatch) -> str:
    home = str(tmp_path / "home")
    monkeypatch.setenv("NEWSDESK_HOME", home)
    return home


def test_item_unknown_id_exits_1(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["item", "item_doesNotExist"])
    assert result.exit_code == 1
    assert "Not found" in result.output


def test_log_shows_source_added_with_user_actor(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    runner.invoke(app, ["add-source", "https://example.com/feed.xml"])
    result = runner.invoke(app, ["log", "--limit", "5"])
    assert result.exit_code == 0
    assert "source_added" in result.output
    assert "[user]" in result.output


def test_digest_daily_empty_database_renders_extractive_briefing(
        tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["digest-daily"])
    assert result.exit_code == 0
    assert "# Newsdesk briefing" in result.output
    assert "extractive" in result.output          # no LLM configured
    assert "0 items in window" in result.output


def test_digest_daily_json_output_is_machine_readable(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["digest-daily", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["method"] == "extractive"
    assert payload["items_in_window"] == 0
    assert payload["worth_following"] == []
    assert payload["window_hours"] == 24


def test_accounts_list_shows_unlinked_defaults(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["accounts", "list"])
    assert result.exit_code == 0
    assert "email/default" in result.output
    assert "twitter/default" in result.output
    assert "youtube-account/default" in result.output
    assert "not linked" in result.output


def test_accounts_grant_and_list_roundtrip(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["accounts", "grant", "email", "main",
                                 "--cap", "read_mail"])
    assert result.exit_code == 0, result.output
    assert "granted email/main: read_mail" in result.output
    listed = runner.invoke(app, ["accounts", "list"])
    assert "email/main" in listed.output


def test_accounts_grant_unknown_kind_fails_nonzero(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["accounts", "grant", "gopher", "main",
                                 "--cap", "dig"])
    assert result.exit_code != 0


def test_accounts_revoke_missing_grant_exits_1(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["accounts", "revoke", "twitter", "ghost"])
    assert result.exit_code == 1
    assert "no grant for twitter/ghost" in result.output


def test_accounts_test_unknown_kind_exits_2(tmp_path, monkeypatch):
    """Unknown kinds are rejected before any connection attempt, with exit
    code 2 and the list of known kinds (previously the Exit(2) raise sat
    inside the except handler and got swallowed into a confusing exit 1)."""
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["accounts", "test", "gopher", "main"])
    assert result.exit_code == 2
    assert "unknown kind 'gopher'" in result.output
    assert "youtube-account" in result.output  # known kinds listed


def test_collect_reports_per_source_line_and_job_summary(
        tmp_path, monkeypatch, energy_feed):
    _home(tmp_path, monkeypatch)
    runner.invoke(app, ["add-source", energy_feed.as_uri()])
    result = runner.invoke(app, ["collect"])
    assert result.exit_code == 0, result.output
    assert "+3 new" in result.output
    assert "job 1: done" in result.output
    again = runner.invoke(app, ["collect"])
    assert "=3 unchanged" in again.output
