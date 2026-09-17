"""Script-writer plugin + registries: ticket-08 contract, guards, fallback."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from newsdesk.cli import app
from newsdesk.llm.base import BaseLLMAdapter, LLMError
from newsdesk.morning.registries import (NOTIFIERS, PUBLISHERS, SCRIPTWRITERS,
                                         TTS_ENGINES, load_plugins)
from newsdesk.morning.script import DEFAULT_VOICE, episode_date, write_sidecar
from newsdesk.morning.scriptwriter import COLD_OPENS, cold_open_text
from newsdesk.pipeline.digest import build_daily_digest
from newsdesk.storage.repo import ItemRepo, LogRepo, SourceRepo, WatchlistRepo
from tests.conftest import make_canonical_item

runner = CliRunner()


def _mk_item(session, source, n: int, *, title: str, text: str,
             age_hours: float, relevance: float | None = None) -> str:
    when = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    item = make_canonical_item(
        url=f"https://x.example/{n}", title=title, text=text,
        id=f"item_scripttest{n:014d}",
        timestamps={"published_at": when.isoformat(),
                    "retrieved_at": when.isoformat()},
        analysis={"relevance": relevance},
        provenance={"content_hash": f"h{n}"},
    )
    outcome, row = ItemRepo(session).upsert(item, source_id=source.id)
    assert outcome == "created"
    return row.id


class FakeWriterAdapter(BaseLLMAdapter):
    """complete() replays ``raw``; counts calls; optionally raises."""

    name = "fake"

    def __init__(self, raw: str | None = None, *, fail: bool = False):
        self.raw = raw
        self.fail = fail
        self.calls: list[str] = []

    def complete(self, system, user, *, max_tokens=1200, temperature=0.2) -> str:
        if self.fail:
            raise LLMError("boom")
        self.calls.append(system + "\n" + user)
        return self.raw or ""

@pytest.fixture
def script_world(session):
    source, _ = SourceRepo(session).add("https://feeds.example/x.xml", kind="rss")
    WatchlistRepo(session).create("dirs")
    WatchlistRepo(session).add_term(1, "agents")
    ids = {
        "top": _mk_item(session, source, 1, title="Top signal: agent harness",
                        text="A novel agent harness mechanism with real evidence. " * 8,
                        age_hours=2, relevance=0.9),
        "h1": _mk_item(session, source, 2, title="Headline one",
                       text="Agents shipped a thing.", age_hours=3, relevance=0.6),
        "h2": _mk_item(session, source, 3, title="Headline two",
                       text="Agents benchmarked a thing.", age_hours=4, relevance=0.5),
        "h3": _mk_item(session, source, 4, title="Headline three",
                       text="Agents scaled a thing.", age_hours=5, relevance=0.4),
        "hype": _mk_item(session, source, 5, title="Big funding round",
                         text="Startup raises a lot.", age_hours=1, relevance=1.5),
    }
    return source, ids


class _AdapterStub:
    """Digest-side stub: verdicts mapped per item id (unmapped default hype)."""

    name = "fake"

    def __init__(self, verdicts_by_id: dict[str, str]):
        self.verdicts_by_id = verdicts_by_id

    def classify_verdicts(self, items):
        return {"verdicts": [
            {"id": i["id"], "verdict": self.verdicts_by_id.get(i["id"], "hype"),
             "reason": "test"} for i in items]}

    def summarize_digest(self, items):
        return {"error": "llm_not_configured"}


def _digest(session, settings, world, verdicts_for):
    _, ids = world
    by_id = {ids[key]: verdict for key, verdict in verdicts_for.items()}
    return build_daily_digest(session, settings, hours=24,
                              adapter=_AdapterStub(by_id))


# -- Registries ---------------------------------------------------------------


def test_registries_defaults_and_loud_failure():
    load_plugins()
    assert SCRIPTWRITERS.get().__name__ == "llm_brief"
    assert TTS_ENGINES.get().__name__ == "edge_tts_synth"  # plugin landed in 03
    assert NOTIFIERS.names() == []  # ships empty by design
    with pytest.raises(KeyError, match="cloudflare-pages"):
        PUBLISHERS.get()  # default named, plugin lands in ticket 06
    with pytest.raises(KeyError, match="unknown SCRIPTWRITERS plugin 'nope'"):
        SCRIPTWRITERS.get("nope")


# -- llm-brief contract -------------------------------------------------------


def test_structure_selection_and_hype_exclusion(session, settings, script_world):
    _, ids = script_world
    digest = _digest(session, settings, script_world,
                     {"top": "technical", "h1": "technical", "h2": "technical",
                      "h3": "technical", "hype": "hype"})
    writer_raw = json.dumps({
        "headlines": [{"id": ids["h1"], "text": "One happened, and it matters."},
                      {"id": ids["h2"], "text": "Two happened, and it matters."},
                      {"id": ids["h3"], "text": "Three happened, and it matters."}],
        "deep_dive": {"id": ids["top"], "text": " ".join(
            ["The problem is real. The approach is novel."] * 60)},
    })
    brief = SCRIPTWRITERS.get()(settings, digest,
                                adapter=FakeWriterAdapter(writer_raw))

    assert brief["method"] == "llm:fake"
    types = [s["type"] for s in brief["sections"]]
    assert types == ["cold_open", "headline", "headline", "headline",
                     "deep_dive", "close"]
    deep = brief["sections"][-2]
    assert deep["item_ids"] == [ids["top"]]  # highest-relevance technical item
    heads = brief["sections"][1:4]
    assert [s["item_ids"][0] for s in heads] == [ids["h1"], ids["h2"], ids["h3"]]
    all_ids = {i for s in brief["sections"] for i in s["item_ids"]}
    assert ids["hype"] not in all_ids  # hype excluded from the script entirely
    assert all(s["voice"] == DEFAULT_VOICE for s in brief["sections"])
    assert brief["stats"]["words"] >= 400  # deep dive dominates the budget


def test_cold_open_deterministic_by_date(session, settings, script_world):
    digest = _digest(session, settings, script_world, {"top": "technical"})
    a = SCRIPTWRITERS.get()(settings, digest, adapter=FakeWriterAdapter(""),
                            date="2026-09-18")
    b = SCRIPTWRITERS.get()(settings, digest, adapter=FakeWriterAdapter(""),
                            date="2026-09-18")
    c = SCRIPTWRITERS.get()(settings, digest, adapter=FakeWriterAdapter(""),
                            date="2026-09-19")
    assert a["sections"][0]["text"] == b["sections"][0]["text"]
    assert a["sections"][0]["text"] in COLD_OPENS
    assert cold_open_text("2026-09-18") == cold_open_text("2026-09-18")
    assert a["date"] == "2026-09-18" and c["date"] == "2026-09-19"


def test_citation_guard_clips_budget_and_falls_back_per_section(
        session, settings, script_world):
    _, ids = script_world
    digest = _digest(session, settings, script_world,
                     {"top": "technical", "h1": "technical"})
    # model returns: an over-budget deep dive, a headline for an id it was
    # never assigned, and nothing for h1
    writer_raw = json.dumps({
        "headlines": [{"id": ids["hype"], "text": "Unassigned id must not pass."},
                      {"id": ids["h1"], "text": " ".join(["word"] * 100)}],
        "deep_dive": {"id": ids["top"], "text": "Too short."},
    })
    brief = SCRIPTWRITERS.get()(settings, digest,
                                adapter=FakeWriterAdapter(writer_raw))
    deep, h1 = brief["sections"][-2], brief["sections"][1]
    assert deep["item_ids"] == [ids["top"]]
    assert len(deep["text"].split()) <= 460  # budget mechanically enforced
    assert h1["item_ids"] == [ids["h1"]]
    assert 0 < len(h1["text"].split()) <= 60  # extractive fill, still clipped
    assigned = [i for s in brief["sections"] for i in s["item_ids"]]
    assert set(assigned) <= {ids["top"], ids["h1"]}  # nothing unassigned leaks in


def test_llm_failure_degrades_to_extractive(session, settings, script_world):
    _, ids = script_world
    digest = _digest(session, settings, script_world,
                     {"top": "technical", "h1": "technical"})
    brief = SCRIPTWRITERS.get()(settings, digest,
                                adapter=FakeWriterAdapter(fail=True))
    assert brief["method"] == "extractive"
    types = [s["type"] for s in brief["sections"]]
    assert types == ["cold_open", "headline", "deep_dive", "close"]
    assert brief["sections"][1]["item_ids"] == [ids["h1"]]
    assert brief["sections"][-2]["item_ids"] == [ids["top"]]


def test_unparseable_output_degrades_to_extractive(session, settings, script_world):
    digest = _digest(session, settings, script_world, {"top": "technical"})
    brief = SCRIPTWRITERS.get()(settings, digest,
                                adapter=FakeWriterAdapter("not json"))
    assert brief["method"] == "extractive"


def test_quiet_day_runs_short(session, settings, script_world):
    _, ids = script_world
    digest = _digest(session, settings, script_world,
                     {k: "hype" for k in ("top", "h1", "h2", "h3")})
    brief = SCRIPTWRITERS.get()(settings, digest, adapter=FakeWriterAdapter(""))
    assert [s["type"] for s in brief["sections"]] == ["cold_open", "close"]
    assert all(s["item_ids"] == [] for s in brief["sections"])
    assert brief["stats"]["est_seconds"] < 30  # never padded


def test_episode_date_is_hkt():
    # 2026-09-18 20:00 UTC is already the 19th in Hong Kong
    utc = datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc)
    assert episode_date(utc) == "2026-09-19"


# -- Sidecar + log + CLI surface -----------------------------------------------


def test_sidecar_shape_and_log(session, settings, script_world):
    _, ids = script_world
    digest = _digest(session, settings, script_world,
                     {"top": "technical", "h1": "technical"})
    brief = SCRIPTWRITERS.get()(settings, digest,
                                adapter=FakeWriterAdapter(fail=True))
    path = write_sidecar(settings, brief["date"], brief)
    assert path.name == f"{brief['date']}-script.json"
    payload = json.loads(path.read_text())
    assert [s["type"] for s in payload["sections"]] == [
        "cold_open", "headline", "deep_dive", "close"]
    for section in payload["sections"]:
        assert set(section) == {"type", "text", "item_ids", "voice", "est_seconds"}

    LogRepo(session).append("morning_brief_built", {
        "date": brief["date"], "method": brief["method"], "stats": brief["stats"],
        "sections": [{"type": s["type"], "item_ids": s["item_ids"],
                      "est_seconds": s["est_seconds"]}
                     for s in brief["sections"]],
        "sidecar": str(path),
    })
    entry = [e for e in LogRepo(session).recent(limit=5)
             if e.action == "morning_brief_built"][0]
    assert entry.detail["sections"][1]["item_ids"] == [ids["h1"]]


def test_cli_script_stages_end_to_end(session, settings, script_world, monkeypatch):
    # full CLI path with no LLM configured: verdicts skip (labeled), the pack
    # falls back to relevance order, the writer goes extractive, the sidecar
    # is written under morning_dir, and the log entry lands.
    _, ids = script_world
    monkeypatch.setenv("NEWSDESK_HOME", str(settings.home))

    result = runner.invoke(app, ["script", "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["method"] == "extractive"
    types = [s["type"] for s in body["sections"]]
    assert types == ["cold_open", "headline", "headline", "headline",
                     "deep_dive", "close"]
    # no verdicts in this path: rank order rules, so the 1.5-relevance item
    # (the funding story) is the deep dive — that's the labeled degradation
    assert body["sections"][-2]["item_ids"] == [ids["hype"]]

    from pathlib import Path
    sidecar = Path(body["sidecar"])
    assert sidecar.exists() and sidecar.name == f"{body['date']}-script.json"
    entries = [e for e in LogRepo(session).recent(limit=5)
               if e.action == "morning_brief_built"]
    assert entries and entries[0].detail["date"] == body["date"]
