"""The workflow catalog (wayfinder tickets 04 + 06).

Pins ticket 04's semantics as built: append-only dense versions (never
reused, never overwritten), computed latest with float/pin refs, required
actor tags with logged mutations, name-level retirement that refuses
loudly at resolution, validate-on-load boundary checks, the structural
keyed-by-name diff, the shipped-descriptor bootstrap, and the CLI verbs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import col, select
from typer.testing import CliRunner

from newsdesk.cli import app
from newsdesk.core.models import LogEntry, WorkflowVersion
from newsdesk.workflow.catalog import (DEFAULT_WORKFLOW, CatalogError,
                                       WorkflowCatalog, diff_descriptors,
                                       ensure_default_catalog, parse_ref)
from newsdesk.workflow.schema import FORMAT_VERSION

runner = CliRunner()


def descriptor(name: str = "test-morning", version: int = 1,
               **overrides) -> dict:
    """A minimal valid v1 descriptor with the canonical six-stage chain."""
    doc = {
        "format_version": FORMAT_VERSION,
        "name": name,
        "version": version,
        "stages": [{"type": t} for t in ("collect", "select", "compose",
                                         "render", "publish", "notify")],
    }
    doc.update(overrides)
    return doc


def log_actions(session, action: str) -> list[LogEntry]:
    return list(session.exec(
        select(LogEntry).where(LogEntry.action == action)
        .order_by(col(LogEntry.id))))


def _corrupt_first_version(session, mutate) -> None:
    """Hand-edit the one stored version row the way the storage channel
    can be changed outside newsdesk's API (ticket 04's boundary case)."""
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(), actor="user")
    row = session.exec(select(WorkflowVersion)).first()
    mutate(row)
    session.add(row)
    session.commit()


# -- refs ----------------------------------------------------------------------


def test_parse_ref_forms():
    assert parse_ref("default-morning") == ("default-morning", None)
    assert parse_ref("default-morning@3") == ("default-morning", 3)
    # explicit @latest and a bare name are the same float
    assert parse_ref("default-morning@latest") == ("default-morning", None)


def test_parse_ref_rejects_malformed():
    with pytest.raises(CatalogError):
        parse_ref("")
    with pytest.raises(CatalogError):
        parse_ref("@3")
    with pytest.raises(CatalogError):
        parse_ref("x@tomorrow")


# -- create_version: append-only, dense, actor-tagged, logged -------------------


def test_first_version_creates_name_row_and_both_log_entries(session):
    catalog = WorkflowCatalog(session)
    version = catalog.create_version(descriptor(), actor="user")
    assert version == 1
    row = catalog.list()[0]
    assert row["name"] == "test-morning" and row["latest"] == 1
    assert row["retired_at"] is None
    created = log_actions(session, "workflow_created")
    assert [e.detail for e in created] == [{"workflow": "test-morning"}]
    assert [e.actor for e in created] == ["user"]
    versioned = log_actions(session, "workflow_version_created")
    assert [e.detail for e in versioned] == [
        {"workflow": "test-morning", "version": 1}]


def test_actor_is_required_and_validated(session):
    catalog = WorkflowCatalog(session)
    with pytest.raises(CatalogError, match="actor"):
        catalog.create_version(descriptor(), actor="crowd")


def test_invalid_document_never_enters(session):
    catalog = WorkflowCatalog(session)
    doc = descriptor()
    doc["stages"][0]["type"] = "transmogrify"
    with pytest.raises(CatalogError, match="transmogrify"):
        catalog.create_version(doc, actor="user")
    assert catalog.list() == []


def test_versions_are_dense_max_plus_one(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(version=1), actor="user")
    with pytest.raises(CatalogError, match="must be 2"):
        catalog.create_version(descriptor(version=3), actor="user")


def test_versions_are_never_reused_or_overwritten(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(title="original"), actor="user")
    with pytest.raises(CatalogError, match="never overwritten"):
        catalog.create_version(descriptor(title="rewrite"), actor="user")
    assert catalog.get("test-morning", 1)["title"] == "original"


def test_retired_name_refuses_new_versions(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(), actor="user")
    catalog.retire("test-morning", actor="user")
    with pytest.raises(CatalogError, match="retired"):
        catalog.create_version(descriptor(version=2), actor="user")


def test_via_rides_the_log_detail(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(), actor="system", via="shipped")
    entry = log_actions(session, "workflow_version_created")[0]
    assert entry.detail == {"workflow": "test-morning", "version": 1,
                            "via": "shipped"}


# -- get / resolve: computed latest, float/pin, loud refusals -------------------


def test_get_returns_the_stored_document(session):
    catalog = WorkflowCatalog(session)
    doc = descriptor()
    catalog.create_version(doc, actor="user")
    assert catalog.get("test-morning", 1) == doc
    assert catalog.get("test-morning", 2) is None
    assert catalog.get("no-such-workflow", 1) is None


def test_resolve_floats_latest_and_pins(session):
    catalog = WorkflowCatalog(session)
    v1 = descriptor(version=1)
    v2 = descriptor(version=2, title="newer")
    catalog.create_version(v1, actor="user")
    catalog.create_version(v2, actor="user")
    assert catalog.resolve("test-morning") == v2          # floats
    assert catalog.resolve("test-morning@latest") == v2   # explicit float
    assert catalog.resolve("test-morning@1") == v1        # pins


def test_resolve_unknown_name_and_version_are_loud(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(), actor="user")
    with pytest.raises(CatalogError, match="unknown workflow"):
        catalog.resolve("no-such-workflow")
    with pytest.raises(CatalogError, match="no version 7"):
        catalog.resolve("test-morning@7")


def test_retired_refuses_resolution_with_since_date(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(), actor="user")
    catalog.retire("test-morning", actor="user")
    with pytest.raises(CatalogError, match=r"retired \(since "):
        catalog.resolve("test-morning")
    # inspection stays possible after retirement — history is not amnesia
    assert catalog.document("test-morning")["name"] == "test-morning"


def test_unretire_reopens_resolution_and_logs(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(), actor="user")
    catalog.retire("test-morning", actor="user")
    catalog.unretire("test-morning", actor="user")
    assert catalog.resolve("test-morning")["name"] == "test-morning"
    assert len(log_actions(session, "workflow_unretired")) == 1


def test_retire_and_unretire_are_idempotent(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(), actor="user")
    catalog.retire("test-morning", actor="user")
    catalog.retire("test-morning", actor="user")
    catalog.unretire("test-morning", actor="agent")
    catalog.unretire("test-morning", actor="agent")
    assert len(log_actions(session, "workflow_retired")) == 1
    assert len(log_actions(session, "workflow_unretired")) == 1
    with pytest.raises(CatalogError):
        catalog.retire("no-such-workflow", actor="user")


# -- validate on load: the DB is a boundary channel -----------------------------


def test_corrupted_storage_is_caught_on_load(session):
    def corrupt(row) -> None:
        row.document = {"format_version": 1, "name": "test-morning",
                        "version": 1, "stages": "not-a-list"}

    _corrupt_first_version(session, corrupt)
    catalog = WorkflowCatalog(session)
    with pytest.raises(CatalogError, match="outside the catalog"):
        catalog.resolve("test-morning")
    with pytest.raises(CatalogError, match="outside the catalog"):
        catalog.get("test-morning", 1)
    with pytest.raises(CatalogError, match="outside the catalog"):
        catalog.versions("test-morning")  # history re-validates too
    with pytest.raises(CatalogError, match="outside the catalog"):
        catalog.document("test-morning")  # inspection is not exempt


def test_identity_mismatch_between_row_and_document_is_caught(session):
    def corrupt(row) -> None:
        # a fresh dict (in-place edits are not flushed; the boundary
        # writer would have replaced the column value)
        row.document = {**row.document, "version": 99}

    _corrupt_first_version(session, corrupt)
    catalog = WorkflowCatalog(session)
    with pytest.raises(CatalogError, match="disagree"):
        catalog.get("test-morning", 1)


def test_returned_documents_are_private_copies(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(title="keep"), actor="user")
    doc = catalog.get("test-morning", 1)
    doc["title"] = "mutated"
    assert catalog.get("test-morning", 1)["title"] == "keep"


# -- list / versions / diff -----------------------------------------------------


def test_list_reports_names_latest_and_retirement(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(name="a-first"), actor="user")
    catalog.create_version(descriptor(name="a-first", version=2),
                           actor="agent")
    catalog.create_version(descriptor(name="b-second"), actor="user")
    catalog.retire("b-second", actor="user")
    rows = catalog.list()
    assert [r["name"] for r in rows] == ["a-first", "b-second"]
    assert rows[0]["latest"] == 2 and rows[0]["retired_at"] is None
    assert rows[1]["latest"] == 1 and rows[1]["retired_at"] is not None


def test_versions_full_history_loud_on_unknown(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(), actor="user")
    catalog.create_version(descriptor(version=2), actor="agent")
    history = catalog.versions("test-morning")
    assert [(v["version"], v["created_by"]) for v in history] == \
        [(1, "user"), (2, "agent")]
    assert all("document" in v and "created_at" in v for v in history)
    with pytest.raises(CatalogError, match="unknown workflow"):
        catalog.versions("no-such-workflow")


def test_catalog_diff_matches_module_level_diff(session):
    catalog = WorkflowCatalog(session)
    catalog.create_version(descriptor(), actor="user")
    catalog.create_version(descriptor(version=2, title="newer"), actor="user")
    assert catalog.diff("test-morning", 1, 2) == diff_descriptors(
        descriptor(), descriptor(version=2, title="newer"))
    with pytest.raises(CatalogError):
        catalog.diff("test-morning", 1, 9)


# -- diff_descriptors: keyed by stage name, one order entry ---------------------


def test_identical_documents_diff_to_empty():
    assert diff_descriptors(descriptor(), descriptor()) == []


def test_scalar_changes_and_added_removed_keys():
    a = descriptor()
    b = descriptor(title="The Morning", version=2)
    b["params"] = {"hours": 24}
    del b["stages"][5]  # remove notify
    diffs = diff_descriptors(a, b)
    by_path = {d["path"]: d for d in diffs}
    assert by_path["/title"] == {"path": "/title", "kind": "added",
                                 "before": None, "after": "The Morning"}
    assert by_path["/version"]["kind"] == "changed"
    assert by_path["/params"]["kind"] == "added"
    assert by_path["/stages/notify"]["kind"] == "removed"
    assert by_path["/stages"]["kind"] == "order"


def test_stage_change_is_keyed_by_name_not_position():
    """A mid-list insert must not cascade 'changed' onto every later
    stage: the changed stage is reported at its name path, the insert as
    one added entry, and the sequence change as one order entry."""
    a = descriptor()
    b = descriptor()
    b["stages"].insert(3, {"type": "collect", "name": "recollect"})
    b["stages"][1]["params"] = {"hours": 12}  # the stage typed 'select'
    diffs = diff_descriptors(a, b)
    kinds = {(d["path"], d["kind"]) for d in diffs}
    assert ("/stages/recollect", "added") in kinds
    assert ("/stages/select/params", "added") in kinds
    assert not any(path in ("/stages/compose", "/stages/render")
                   for path, _ in kinds)
    order = next(d for d in diffs if d["path"] == "/stages")
    assert order == {
        "path": "/stages", "kind": "order",
        "before": ["collect", "select", "compose", "render", "publish",
                   "notify"],
        "after": ["collect", "select", "compose", "recollect", "render",
                  "publish", "notify"]}


def test_stage_params_leaf_change_uses_pointer_path():
    a = descriptor()
    b = descriptor()
    b["stages"][1]["checks"] = [
        {"name": "word_budget", "params": {"total": 500}}]
    diffs = diff_descriptors(a, b)
    added = [d for d in diffs
             if d["kind"] == "added"
             and d["path"].startswith("/stages/select/checks")]
    assert added


def test_cross_name_diffs_are_refused():
    with pytest.raises(CatalogError, match="one workflow"):
        diff_descriptors(descriptor(), descriptor(name="other-morning"))


def test_invalid_documents_are_refused():
    bad = descriptor()
    bad["stages"][0]["type"] = "transmogrify"
    with pytest.raises(Exception, match="invalid workflow descriptor"):
        diff_descriptors(bad, bad)


# -- the shipped-descriptor bootstrap -------------------------------------------


# -- save gates: bindings + size (ticket 13/14, shared with the engine) ---------


def test_unbindable_document_is_refused_at_save(session):
    """The engine's static pre-flight rules run at save too — one
    implementation (workflow.bindings), two callers. A document that
    could never run never enters the catalog."""
    catalog = WorkflowCatalog(session)
    doc = descriptor()
    doc["stages"][2]["plugin"] = "ghost"
    with pytest.raises(CatalogError, match="unknown SCRIPTWRITERS plugin"):
        catalog.create_version(doc, actor="user")

    early = descriptor()
    early["stages"][1]["checks"] = [
        {"name": "duration_band",
         "params": {"min_seconds": 1, "max_seconds": 2}}]
    with pytest.raises(CatalogError, match="needs the 'audio' artifact"):
        catalog.create_version(early, actor="user")

    unknown_param = descriptor()
    unknown_param["stages"][2]["params"] = {"voice": "nope"}
    with pytest.raises(CatalogError, match="unknown params voice"):
        catalog.create_version(unknown_param, actor="user")

    # notify fans out over every notifier: no plugin to own params, so ANY
    # param is unknown there (the engine's old default, kept by the shared
    # validator)
    notify_params = descriptor()
    notify_params["stages"][5]["params"] = {"webhook": "https://…"}
    with pytest.raises(CatalogError, match="unknown params webhook"):
        catalog.create_version(notify_params, actor="user")

    assert session.exec(select(WorkflowVersion)).all() == []


def test_oversized_document_is_refused_at_save(session):
    """The 64 KiB boundary: v1 documents are ~1 KB and schema-closed, so a
    near-cap document is a payload, refused before log/DB/tool results."""
    catalog = WorkflowCatalog(session)
    with pytest.raises(CatalogError, match="cap 65536"):
        catalog.create_version(descriptor(title="x" * 70_000), actor="user")
    # exactly at the cap is legal
    doc = descriptor(title="x" * 40_000)
    catalog.create_version(doc, actor="user")


# -- the shipped default ---------------------------------------------------------


def test_bootstrap_imports_shipped_default_once(session):
    ensure_default_catalog(session)
    catalog = WorkflowCatalog(session)
    assert catalog.get(DEFAULT_WORKFLOW, 1)["name"] == DEFAULT_WORKFLOW
    versioned = log_actions(session, "workflow_version_created")
    assert versioned[0].actor == "system"
    assert versioned[0].detail["via"] == "shipped"
    ensure_default_catalog(session)  # second call: no new log entries
    assert len(log_actions(session, "workflow_created")) == 1
    assert len(log_actions(session, "workflow_version_created")) == 1


def test_bootstrap_never_touches_an_existing_row(session):
    catalog = WorkflowCatalog(session)
    mine = descriptor(name=DEFAULT_WORKFLOW, title="locally evolved")
    catalog.create_version(mine, actor="user")
    ensure_default_catalog(session)
    # the DB is the living data: the shipped file did not win
    assert catalog.get(DEFAULT_WORKFLOW, 1)["title"] == "locally evolved"


# -- the CLI surface ------------------------------------------------------------


def _home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("NEWSDESK_HOME", str(home))
    return home


def _write(tmp_path: Path, doc: dict, name: str = "doc.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def test_workflow_cli_lifecycle(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)

    v1 = _write(tmp_path, descriptor(title="first"), "v1.json")
    result = runner.invoke(app, ["workflow", "create", str(v1)])
    assert result.exit_code == 0, result.output
    assert "test-morning@1" in result.output

    v2 = _write(tmp_path, descriptor(title="second", version=2), "v2.json")
    result = runner.invoke(app, ["workflow", "create", str(v2)])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["workflow", "list"])
    assert result.exit_code == 0
    assert "test-morning" in result.output and "2" in result.output

    result = runner.invoke(app, ["workflow", "get", "test-morning@1"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["title"] == "first"

    result = runner.invoke(app, ["workflow", "history", "test-morning"])
    assert result.exit_code == 0
    assert "user" in result.output and "v2" in result.output

    result = runner.invoke(app, ["workflow", "diff", "test-morning", "1", "2"])
    assert result.exit_code == 0, result.output
    assert '"kind": "changed"' in result.output

    result = runner.invoke(app, ["workflow", "retire", "test-morning"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["workflow", "list"])
    assert "retired" in result.output
    # inspection still works on a retired name; resolution would refuse
    result = runner.invoke(app, ["workflow", "get", "test-morning"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["workflow", "unretire", "test-morning"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["workflow", "list"])
    assert "retired" not in result.output


def test_workflow_cli_create_rejects_bad_documents(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    bad = descriptor()
    bad["stages"][0]["type"] = "transmogrify"
    path = _write(tmp_path, bad)
    result = runner.invoke(app, ["workflow", "create", str(path)])
    assert result.exit_code == 1
    assert "error:" in result.output


def test_workflow_cli_unknown_refs_exit_1(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    for args in (["get", "no-such@1"], ["history", "no-such"],
                 ["diff", "no-such", "1", "2"],
                 ["retire", "no-such"], ["unretire", "no-such"]):
        result = runner.invoke(app, ["workflow", *args])
        assert result.exit_code == 1, args
        assert "error:" in result.output


def test_workflow_cli_diff_with_no_differences(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    path = _write(tmp_path, descriptor())
    runner.invoke(app, ["workflow", "create", str(path)])
    result = runner.invoke(app, ["workflow", "diff", "test-morning", "1", "1"])
    assert result.exit_code == 0
    assert "no differences" in result.output


def test_workflow_cli_mutations_are_logged_as_user(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    path = _write(tmp_path, descriptor())
    runner.invoke(app, ["workflow", "create", str(path)])
    runner.invoke(app, ["workflow", "retire", "test-morning"])
    result = runner.invoke(app, ["log", "--limit", "10"])
    assert result.exit_code == 0
    assert "[user] workflow_version_created" in result.output
    assert "[user] workflow_retired" in result.output


def test_morning_refuses_a_retired_default(tmp_path, monkeypatch):
    """The morning seam: bootstrap + catalog resolve + loud retirement
    refusal — the CatalogError surfaces as the CLI's error exit."""
    _home(tmp_path, monkeypatch)
    # any workflow verb bootstraps the shipped default first
    result = runner.invoke(app, ["workflow", "retire", DEFAULT_WORKFLOW])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["morning"])
    assert result.exit_code == 1
    assert "retired" in result.output


def test_cli_workflow_list_boots_the_shipped_default(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    result = runner.invoke(app, ["workflow", "list"])
    assert result.exit_code == 0
    assert DEFAULT_WORKFLOW in result.output


def test_cli_get_on_a_nameless_version_row_is_loud_not_a_crash(
        tmp_path, monkeypatch):
    """A hand-edited DB can hold a workflows row with no versions (the
    API cannot create one); `workflow get` must fail with the CLI error,
    never an IndexError."""
    from newsdesk.core.models import Workflow

    home = _home(tmp_path, monkeypatch)
    from newsdesk.config import Settings
    from newsdesk.storage.db import Database

    settings = Settings(home=home)
    settings.ensure_dirs()
    with Database(settings).session() as s:
        s.add(Workflow(name="ghost"))
        s.commit()
    result = runner.invoke(app, ["workflow", "get", "ghost"])
    assert result.exit_code == 1
    assert "no versions" in result.output
