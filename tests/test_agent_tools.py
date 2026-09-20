"""W5 agent tools: the host-neutral implementations in agents.tools
(wayfinder tickets 13 + 14).

Ticket 14's acceptance, pinned: every tool works through one
implementation (the HTTP/MCP surfaces are tested by the sync test and by
``test_api``/``test_mcp``); a chat edit stores a validated new version and
the next run uses it; the run tool provably cannot publish (every
publisher and wrangler patched to raise, ``publish_dir`` untouched, and
no publish/dry-run parameter exists to flip); every mutation lands in the
log as ``actor="agent"`` with the adapter's ``via``. Fully offline.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone

import pytest

from newsdesk.agents import tools
from newsdesk.storage.repo import ItemRepo, LogRepo, SourceRepo
from newsdesk.workflow.catalog import CatalogError
from newsdesk.workflow.engine import WorkflowRunError
from tests.conftest import FEEDS, make_canonical_item

TOKEN = "3f9a1c77b2d84e05a1c3f6d2b9e08417"
DATE = "2026-09-20"
_FRAME = b"\xff\xf3\x64\x00" + b"\x00" * 140


def _fake_edge_save(text, voice, path):
    path.write_bytes(_FRAME * (40 + len(text)))


@pytest.fixture
def offline(settings, monkeypatch):
    settings.feed_token = TOKEN
    settings.tts_pace = 0.0
    monkeypatch.setattr("newsdesk.morning.edgetts._edge_save", _fake_edge_save)
    return settings


def _descriptor(name: str, version: int, **overrides) -> dict:
    """A minimal valid descriptor on the default chain's shape."""
    doc: dict = {
        "format_version": 1, "name": name, "version": version,
        "stages": [
            {"type": "collect"}, {"type": "select"},
            {"type": "compose", "name": "script", "params": {},
             "checks": [{"name": "section_allowlist",
                         "params": {"allow": ["cold_open", "headline",
                                              "deep_dive", "close"]}}]},
            {"type": "render"}, {"type": "publish"}, {"type": "notify"},
        ],
    }
    doc.update(overrides)
    return doc


def _mk_item(session, source, n: int, *, title: str, publisher: str) -> str:
    when = datetime.now(timezone.utc) - timedelta(hours=1 + n / 10)
    item = make_canonical_item(
        url=f"https://{publisher.lower().replace(' ', '')}.example/{n}",
        title=title, text="A novel mechanism, measured. " * 4,
        id=f"item_tooltest{n:014d}",
        source={"publisher": publisher, "url": f"https://x.example/{n}",
                "kind": "article", "author": None},
        timestamps={"published_at": when.isoformat(),
                    "retrieved_at": when.isoformat()},
        provenance={"content_hash": f"h{n}"},
    )
    outcome, row = ItemRepo(session).upsert(item, source_id=source.id)
    assert outcome == "created"
    return row.id


def _log(session) -> list[dict]:
    rows = LogRepo(session).recent(limit=100)
    return [e.to_json() for e in reversed(list(rows))]


# -- workflows: the chat-edit round trip ------------------------------------------


def test_chat_edit_stores_a_version_the_next_run_uses(session, offline):
    from newsdesk.workflow.catalog import WorkflowCatalog

    created = tools.create_workflow(session, offline, _descriptor("chat", 1),
                                    via="mcp")
    assert created["validation"] == {"schema": True, "bindings": True}
    created_v2 = tools.create_workflow(
        session, offline, _descriptor("chat", 2, title="tighter"), via="mcp")
    assert created_v2["version"] == 2  # dense: max + 1, never reused

    # the next scheduled run floats the name: the CLI resolves the same
    # ref the tool's run tool does — version 2 is what runs now
    assert WorkflowCatalog(session).resolve("chat")["version"] == 2
    report = tools.run_workflow(session, offline, "chat")
    assert report["workflow"] == {"name": "chat", "version": 2}
    assert report["outcome"] == "dry_run"  # the tool cannot publish

    # every mutation actor-tagged: the log says who and from where
    entries = [(e["action"], e["actor"], e["detail"].get("via"))
               for e in _log(session)]
    assert ("workflow_created", "agent", "mcp") in entries
    assert ("workflow_version_created", "agent", "mcp") in entries


def test_workflow_tool_reads_summaries_and_stores_documents(session, offline):
    tools.create_workflow(session, offline, _descriptor("alpha", 1), via="http")
    tools.create_workflow(session, offline, _descriptor("beta", 1), via="http")

    found = tools.list_workflows(session, offline, query="ALPHA")
    assert [i["name"] for i in found["items"]] == ["alpha"]
    assert found["truncated"] is False
    assert tools.list_workflows(session, offline, query="zzz")["items"] == []

    doc = tools.get_workflow(session, offline, "alpha")
    assert doc["resolved_version"] == 1 and doc["retired"] is False
    assert doc["stages"][0]["type"] == "collect"
    with pytest.raises(CatalogError, match="already pins version"):
        tools.get_workflow(session, offline, "alpha@1", version=2)

    diff = tools.diff_workflow(session, offline, "alpha", 1, 1)
    assert diff["changes"] == []
    tools.create_workflow(session, offline,
                          _descriptor("alpha", 2, title="renamed"),
                          via="http")
    diff = tools.diff_workflow(session, offline, "alpha", 1, 2)
    assert diff["changes"][0]["path"] == "/title"
    assert diff["changes"][0]["kind"] == "added"

    tools.retire_workflow(session, offline, "beta", via="http")
    assert tools.get_workflow(session, offline, "beta")["retired"] is True
    # retired names drop out of the default listing but stay inspectable
    assert {i["name"] for i in tools.list_workflows(
        session, offline)["items"]} == {"default-morning", "alpha"}
    retired_view = tools.list_workflows(session, offline,
                                        include_retired=True)
    assert {i["name"] for i in retired_view["items"]} >= \
        {"default-morning", "alpha", "beta"}
    with pytest.raises(CatalogError, match="retired"):
        tools.run_workflow(session, offline, "beta")
    tools.unretire_workflow(session, offline, "beta", via="http")
    assert tools.run_workflow(session, offline, "beta")["outcome"] == "dry_run"


def test_create_workflow_refuses_what_could_never_run(session, offline):
    """Validation-on-save lives in the repo layer: schema, static bindings
    (the engine's pre-flight rules) and the 64 KiB cap — every surface
    inherits them, so the tool adds nothing of its own."""
    tools.create_workflow(session, offline, _descriptor("keep", 1), via="mcp")

    unknown_plugin = _descriptor("broken", 1)
    unknown_plugin["stages"][2]["plugin"] = "ghost"
    with pytest.raises(CatalogError,
                       match="unknown SCRIPTWRITERS plugin 'ghost'"):
        tools.create_workflow(session, offline, unknown_plugin, via="mcp")

    early_check = _descriptor("broken", 1)
    early_check["stages"][1]["checks"] = [
        {"name": "duration_band",
         "params": {"min_seconds": 1, "max_seconds": 2}}]
    with pytest.raises(CatalogError, match="needs the 'audio' artifact"):
        tools.create_workflow(session, offline, early_check, via="mcp")

    no_rubric = _descriptor("broken", 1)
    no_rubric["stages"][1]["plugin"] = "top-k-interesting"
    no_rubric["stages"][1]["params"] = {"k": 3}
    with pytest.raises(CatalogError, match="requires a 'rubric' param"):
        tools.create_workflow(session, offline, no_rubric, via="mcp")

    bloated = _descriptor("broken", 1, title="x" * 70_000)
    with pytest.raises(CatalogError, match="cap 65536"):
        tools.create_workflow(session, offline, bloated, via="mcp")

    assert [i["name"] for i in tools.list_workflows(
        session, offline, include_retired=True)["items"]
        if i["name"] != "default-morning"] == ["keep"]


def test_create_workflow_smoke_is_opt_in_and_refuses_the_save(session,
                                                              offline):
    """dry_run=true runs the candidate once against the current database
    BEFORE storing; a stage failure refuses the save. The failing
    document demands a section the extractive writer never emits — a
    fatal check, so the smoke fails loudly."""
    good = _descriptor("smoked", 1)
    result = tools.create_workflow(session, offline, good, dry_run=True,
                                   via="mcp")
    assert result["validation"] == {"schema": True, "bindings": True,
                                    "smoke": True}
    assert tools.get_workflow(session, offline, "smoked")["version"] == 1

    doomed = _descriptor("doomed", 1)
    doomed["stages"][2]["checks"] = [
        {"name": "section_allowlist", "on_fail": "fail",
         "params": {"allow": ["cold_open"]}}]  # the writer emits no cold_open
    with pytest.raises(CatalogError, match="smoke dry-run failed"):
        tools.create_workflow(session, offline, doomed, dry_run=True,
                              via="mcp")
    names = [i["name"] for i in tools.list_workflows(
        session, offline, include_retired=True)["items"]]
    assert "doomed" not in names  # the save was refused
    # ...and without the flag the same broken document still cannot save
    # (it would only fail later, at its first real run)


# -- rubrics + score_preview ----------------------------------------------------


def _draft(all_proxied: bool) -> dict:
    dimensions = [
        {"name": "fit", "description": "watchlist fit", "weight": 0.6,
         "proxy": "relevance"},
        {"name": "fresh", "description": "freshness", "weight": 0.4,
         "proxy": "recency"},
    ]
    if not all_proxied:
        dimensions.append({"name": "depth", "description": "mechanism",
                           "weight": 2.0})
    return {"format_version": 1, "name": "draft", "version": 1,
            "dimensions": dimensions, "thresholds": {"min_score": 0.0}}


def test_score_preview_scores_the_current_candidates_and_writes_nothing(
        session, settings):
    source, _ = SourceRepo(session).add(
        (FEEDS / "sample-energy.xml").as_uri(), kind="rss")
    first = _mk_item(session, source, 1, title="Grid storage breakthrough",
                     publisher="Alpha Wire")
    second = _mk_item(session, source, 2, title="Grid storage breakthrough",
                      publisher="Beta Daily")  # same story, second outlet

    preview = tools.score_preview(session, settings, _draft(all_proxied=True))
    assert preview["rubric_ref"] == "draft@1"
    assert preview["method"] == "mechanical"  # all-proxied: no model asked
    assert preview["llm_error"] is None
    assert {s["id"] for s in preview["scores"]} == {first, second}
    by_id = {s["id"]: s for s in preview["scores"]}
    assert by_id[first]["outlets"] == by_id[second]["outlets"] == 2
    # same story, same corroboration; the copy published an hour earlier
    # (fresher) scores a touch higher on the recency proxy
    assert by_id[first]["total"] > by_id[second]["total"]
    assert all(set(s["dimensions"]) == {"fit", "fresh"}
               for s in preview["scores"])
    assert all(len(d["reason"]) <= 200
               for s in preview["scores"]
               for d in s["dimensions"].values())

    # an unproxied dimension with no LLM configured: the all-or-nothing
    # mechanical fallback, cause visible in llm_error — never silent
    with_model = tools.score_preview(session, settings, _draft(False))
    assert with_model["method"] == "mechanical"
    assert with_model["llm_error"]

    # writes nothing: no catalog row for the draft, no collection pass
    assert [i["name"] for i in tools.list_rubrics(session, settings)
            ["items"]] == ["default"]
    # a stored ref works the same way; unproxied default + no key → the
    # fallback's cause is visible there too
    assert tools.score_preview(session, settings, "default@1")[
        "llm_error"] is not None


def test_rubric_tools_round_trip_and_diff_is_order_sensitive(session,
                                                             offline):
    tools.create_rubric(session, offline, _draft(True), via="mcp")
    assert [i["dimensions"] for i in tools.list_rubrics(
        session, offline)["items"] if i["name"] == "draft"] == \
        [["fit", "fresh"]]

    reordered = _draft(True)
    reordered["version"] = 2
    reordered["dimensions"] = list(reversed(reordered["dimensions"]))
    tools.create_rubric(session, offline, reordered, via="mcp")
    changes = tools.diff_rubric(session, offline, "draft", 1, 2)
    kinds = {(c["path"], c["kind"]) for c in changes["changes"]}
    assert ("/dimensions", "order") in kinds  # the reorder is one entry

    tools.retire_rubric(session, offline, "draft", via="mcp")
    # retirement refuses runs, not reading: inspection semantics, like the
    # workflow catalog's document()
    assert tools.get_rubric(session, offline, "draft")["retired"] is True
    entries = [(e["action"], e["actor"], e["detail"].get("via"))
               for e in _log(session)]
    assert ("rubric_created", "agent", "mcp") in entries
    assert ("rubric_retired", "agent", "mcp") in entries


# -- stations --------------------------------------------------------------------


def test_station_tools_round_trip_and_immutability(session, offline):
    row = tools.create_station(session, offline, "papers", "default-morning",
                               path_segment="papers", description="research",
                               feed={"title": "Paper Trail"}, via="mcp")
    assert row["name"] == "papers" and row["path_segment"] == "papers"

    with pytest.raises(CatalogError, match="unknown keys unknown_key"):
        tools.create_station(session, offline, "nope", "default-morning",
                             feed={"unknown_key": 1}, via="mcp")

    detail = tools.get_station(session, offline, "papers")
    assert detail["feed_identity"]["title"] == "Paper Trail"
    assert detail["feed_identity"]["category"]  # NULL fell back to constants
    assert "terms" in detail["scope"]

    # full-document update: omitted fields clear, name/path are not params
    updated = tools.update_station(session, offline, "papers",
                                   "default-morning",
                                   feed={"title": "Paper Trail v2"},
                                   via="http")
    assert updated["feed_title"] == "Paper Trail v2"
    assert updated["description"] is None
    assert updated["path_segment"] == "papers"
    assert "path_segment" not in inspect.signature(
        tools.update_station).parameters

    tools.retire_station(session, offline, "papers", via="http")
    assert tools.list_stations(session, offline)["total"] == 1
    assert tools.list_stations(session, offline,
                               include_retired=False)["total"] == 0
    # a retired station refuses runs — the failure is the engine's loud
    # pre-flight (zero work), not a silent skip
    with pytest.raises(WorkflowRunError, match="retired"):
        tools.run_workflow(session, offline, "default-morning",
                           station="papers")
    tools.unretire_station(session, offline, "papers", via="http")
    entries = [(e["action"], e["actor"], e["detail"].get("via"))
               for e in _log(session)]
    assert ("station_created", "agent", "mcp") in entries
    assert ("station_updated", "agent", "http") in entries
    assert ("station_retired", "agent", "http") in entries


# -- the run tool's structural guarantee ---------------------------------------------


def test_run_workflow_tool_cannot_publish(session, offline, monkeypatch):
    """The acceptance proof: every publisher and the wrangler subprocess
    patched to raise — the tool still returns a complete dry-run report,
    and ``publish_dir`` holds nothing it could have written. The
    structural half: there is no publish or dry-run parameter to flip."""

    def no_emission(*args, **kwargs):
        raise RuntimeError("NO EMISSION")

    monkeypatch.setattr("newsdesk.morning.publish.publish_local", no_emission)
    monkeypatch.setattr("newsdesk.morning.cloudflare.publish_cloudflare",
                        no_emission)
    monkeypatch.setattr("newsdesk.morning.cloudflare._wrangler_deploy",
                        no_emission)

    params = inspect.signature(tools.run_workflow).parameters
    assert "publish" not in params and "dry_run" not in params

    report = tools.run_workflow(session, offline, "default-morning",
                                date=DATE)
    assert report["outcome"] == "dry_run"
    assert set(report["stages"]) == {"collect", "digest", "script", "tts"}
    assert report["stages"]["tts"]["chunks"] > 0
    assert report["sidecar"].endswith(f"{DATE}/{DATE}-script.json")
    assert offline.publish_dir.exists() is False or \
        list(offline.publish_dir.rglob("*")) == []

    verbose = tools.run_workflow(session, offline, "default-morning",
                                 date=DATE, verbose=True)
    assert verbose["outcome"] == "dry_run"
    assert "scores" not in json.dumps(report)  # summarized: receipts only


def test_run_tool_works_for_a_station_and_reports_its_scope(session, offline):
    from newsdesk.workflow.stations import StationRepo

    StationRepo(session).create("papers", actor="user", watchlist_id=None,
                                path_segment="papers")
    report = tools.run_workflow(session, offline, "default-morning",
                                station="papers", date=DATE)
    assert report["station"] == "papers"
    assert report["outcome"] == "dry_run"
    assert report["sidecar"].endswith(f"papers/{DATE}/{DATE}-script.json")
