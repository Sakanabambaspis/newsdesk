"""Workflow descriptor schema v1 (wayfinder ticket 01).

The descriptor is the first deep interface of the workflow-module
architecture: pure data hiding "what a workflow is". These tests pin the
schema contract: format gate, closed stage vocabulary, Cordis chain
coherence, and the closed set of named deterministic checks.
"""

from __future__ import annotations

import json

import pytest

from newsdesk.workflow.schema import (DescriptorError, FORMAT_VERSION,
                                      require_valid, validate_descriptor,
                                      workflow_descriptor_path)


def base_descriptor() -> dict:
    """The canonical six-stage chain, minimal and valid."""
    return {
        "format_version": FORMAT_VERSION,
        "name": "test-morning",
        "version": 1,
        "stages": [{"type": t} for t in ("collect", "select", "compose",
                                         "render", "publish", "notify")],
    }


def test_base_descriptor_is_valid():
    assert validate_descriptor(base_descriptor()) == []


def test_default_morning_descriptor_ships_valid():
    path = workflow_descriptor_path("default-morning", 1)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert validate_descriptor(doc) == []
    require_valid(doc)
    # the shipped default encodes today's chain, in order
    assert [s["type"] for s in doc["stages"]] == [
        "collect", "select", "compose", "render", "publish", "notify"]


def test_format_version_gate():
    doc = base_descriptor()
    del doc["format_version"]
    assert any("format_version" in e for e in validate_descriptor(doc))
    doc = base_descriptor() | {"format_version": 2}
    errors = validate_descriptor(doc)
    assert any("format_version" in e and "1" in e for e in errors)


def test_unknown_top_level_key_rejected():
    doc = base_descriptor() | {"trigger": "cron"}
    assert any("trigger" in e for e in validate_descriptor(doc))


def test_name_and_version_rules():
    doc = base_descriptor() | {"name": "Bad Name"}
    assert validate_descriptor(doc)
    doc = base_descriptor() | {"version": 0}
    assert validate_descriptor(doc)
    doc = base_descriptor() | {"version": "1"}
    assert validate_descriptor(doc)


def test_unknown_stage_type_rejected():
    doc = base_descriptor()
    doc["stages"][2]["type"] = "transmogrify"
    errors = validate_descriptor(doc)
    assert any("transmogrify" in e for e in errors)


def test_stage_must_be_object_with_known_keys_only():
    doc = base_descriptor()
    doc["stages"][0] = "collect"
    assert validate_descriptor(doc)
    doc = base_descriptor()
    doc["stages"][0]["secret"] = True
    assert any("secret" in e for e in validate_descriptor(doc))


def test_empty_stage_list_rejected():
    doc = base_descriptor() | {"stages": []}
    assert validate_descriptor(doc)


def test_duplicate_stage_names_rejected():
    doc = base_descriptor()
    doc["stages"][0]["name"] = "grab"
    doc["stages"][1]["name"] = "grab"
    assert any("grab" in e for e in validate_descriptor(doc))


def test_default_stage_name_is_its_type():
    doc = base_descriptor()
    doc["stages"][0]["name"] = "select"  # collides with stages[1]'s default
    assert validate_descriptor(doc)


def test_chain_coherence_requires_provided_artifacts():
    # compose before select: "digest" never provided at that point
    doc = base_descriptor()
    doc["stages"] = [doc["stages"][2], doc["stages"][1]] + doc["stages"][3:]
    errors = validate_descriptor(doc)
    assert any("digest" in e for e in errors)


def test_missing_collect_makes_chain_incoherent():
    doc = base_descriptor()
    doc["stages"] = doc["stages"][1:]
    assert any("collection" in e for e in validate_descriptor(doc))


def test_plugin_ref_is_string_or_absent():
    doc = base_descriptor()
    doc["stages"][2]["plugin"] = "llm-brief"
    assert validate_descriptor(doc) == []
    doc["stages"][2]["plugin"] = 7
    assert validate_descriptor(doc)


def test_params_must_be_object():
    doc = base_descriptor()
    doc["stages"][1]["params"] = {"hours": 24}
    assert validate_descriptor(doc) == []
    doc["stages"][1]["params"] = [24]
    assert validate_descriptor(doc)


def test_unknown_check_name_rejected():
    doc = base_descriptor()
    doc["stages"][2]["checks"] = [{"name": "vibes", "params": {}}]
    assert any("vibes" in e for e in validate_descriptor(doc))


def test_section_allowlist_check_params():
    doc = base_descriptor()
    check = {"name": "section_allowlist",
             "params": {"allow": ["cold_open", "headline", "deep_dive",
                                  "close"]}}
    doc["stages"][2]["checks"] = [check]
    assert validate_descriptor(doc) == []
    check["params"]["allow"] = ["cold_open", "rant"]  # unknown section type
    assert validate_descriptor(doc)
    check["params"]["allow"] = []
    assert validate_descriptor(doc)


def test_word_budget_check_params():
    doc = base_descriptor()
    check = {"name": "word_budget",
             "params": {"per_section": {"headline": 60, "deep_dive": 460}}}
    doc["stages"][2]["checks"] = [check]
    assert validate_descriptor(doc) == []
    check["params"] = {"total": 800}
    assert validate_descriptor(doc) == []
    check["params"] = {}  # neither per_section nor total
    assert validate_descriptor(doc)
    check["params"] = {"per_section": {"editorial": 60}}
    assert validate_descriptor(doc)
    check["params"] = {"total": -1}
    assert validate_descriptor(doc)


def test_duration_band_check_params():
    doc = base_descriptor()
    check = {"name": "duration_band",
             "params": {"min_seconds": 60, "max_seconds": 600}}
    doc["stages"][3]["checks"] = [check]
    assert validate_descriptor(doc) == []
    check["params"] = {"min_seconds": 600, "max_seconds": 60}
    assert validate_descriptor(doc)
    check["params"] = {"min_seconds": 60}
    assert validate_descriptor(doc)


def test_distinct_stories_and_diversity_floor_params():
    doc = base_descriptor()
    doc["stages"][1]["checks"] = [
        {"name": "distinct_stories", "params": {"min_distinct": 3}},
        {"name": "diversity_floor", "params": {"min_themes": 2}},
    ]
    assert validate_descriptor(doc) == []
    doc["stages"][1]["checks"][0]["params"] = {"min_distinct": 0}
    assert validate_descriptor(doc)
    doc["stages"][1]["checks"][1]["params"] = {}
    assert validate_descriptor(doc)


def test_archive_intact_takes_no_params():
    doc = base_descriptor()
    check = {"name": "archive_intact", "on_fail": "fail"}
    doc["stages"][4]["checks"] = [check]
    assert validate_descriptor(doc) == []
    check["params"] = {"lenient": True}
    assert validate_descriptor(doc)


def test_check_on_fail_policy_enum():
    doc = base_descriptor()
    check = {"name": "word_budget", "params": {"total": 800}}
    doc["stages"][2]["checks"] = [check]
    for policy in (None, "repair", "fail"):
        if policy is None:
            check.pop("on_fail", None)
        else:
            check["on_fail"] = policy
        assert validate_descriptor(doc) == [], policy
    check["on_fail"] = "ignore"
    assert validate_descriptor(doc)


def test_require_valid_raises_descriptor_error():
    doc = base_descriptor() | {"format_version": 99}
    with pytest.raises(DescriptorError):
        require_valid(doc)


def test_loop_policy():
    doc = base_descriptor() | {"loop_policy": {"max_attempts": 2}}
    assert validate_descriptor(doc) == []
    doc["loop_policy"] = {"max_attempts": 1}
    assert validate_descriptor(doc) == []
    doc["loop_policy"] = {"max_attempts": 3}  # bounded repair is <= 2
    assert validate_descriptor(doc)
    doc["loop_policy"] = {"max_attempts": 0}
    assert validate_descriptor(doc)
    doc["loop_policy"] = {"surprise": True}
    assert validate_descriptor(doc)
