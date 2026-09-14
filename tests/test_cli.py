"""End-to-end over the CLI, in-process, against fixture feeds."""

from __future__ import annotations

from typer.testing import CliRunner

from newsdesk.cli import app
from newsdesk.core.ids import item_id_for

runner = CliRunner()


def test_cli_end_to_end(tmp_path, monkeypatch, energy_feed, tech_feed):
    monkeypatch.setenv("NEWSDESK_HOME", str(tmp_path / "home"))

    result = runner.invoke(app, ["add-source", energy_feed.as_uri()])
    assert result.exit_code == 0, result.output
    assert "[1]" in result.output

    result = runner.invoke(app, ["list-sources"])
    assert result.exit_code == 0
    assert "sample-energy.xml" in result.output

    result = runner.invoke(app, ["collect"])
    assert result.exit_code == 0, result.output
    assert "+3 new" in result.output

    # Idempotent: nothing new on a second pass
    result = runner.invoke(app, ["collect"])
    assert "+0 new" in result.output
    assert "=3 unchanged" in result.output

    result = runner.invoke(app, ["search", "offshore wind auction"])
    assert result.exit_code == 0
    assert "offshore-wind-auction" in result.output

    wind_id = item_id_for("https://energy.example.com/stories/offshore-wind-auction")
    result = runner.invoke(app, ["item", wind_id])
    assert result.exit_code == 0
    assert '"retrieved_at"' in result.output
    assert '"content_hash"' in result.output

    result = runner.invoke(app, ["log"])
    assert result.exit_code == 0
    assert "item_created" in result.output
    assert "job_finished" in result.output


def test_cli_search_no_match(tmp_path, monkeypatch, energy_feed):
    monkeypatch.setenv("NEWSDESK_HOME", str(tmp_path / "home"))
    runner.invoke(app, ["add-source", energy_feed.as_uri()])
    runner.invoke(app, ["collect"])
    result = runner.invoke(app, ["search", "zzznotathing"])
    assert result.exit_code == 0
    assert "No matches" in result.output
