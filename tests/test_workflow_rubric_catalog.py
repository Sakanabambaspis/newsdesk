"""The rubric catalog: ticket-04 semantics over rubric documents (W3).

The selection strategies name a rubric by ref string; this catalog is the
only resolution path (wayfinder ticket 07 fixed the contract, ticket 09
built the storage). Same rules as the workflow catalog: dense immutable
versions, computed latest, required actor on every mutation, retirement
that refuses loudly, validation on save AND on load, and the shipped
``default@1`` bootstrapping once then freezing.
"""

from __future__ import annotations

import pytest

from newsdesk.core.models import Rubric, RubricVersion
from newsdesk.storage.repo import LogRepo
from newsdesk.workflow.catalog import CatalogError
from newsdesk.workflow.rubric import (RUBRIC_FORMAT_VERSION,
                                      load_shipped_rubric, rubric_ref)
from newsdesk.workflow.rubric_catalog import (RubricCatalog,
                                              diff_rubrics,
                                              ensure_default_rubric_catalog)


def base_rubric(name: str = "taste", version: int = 1) -> dict:
    """One minimal valid rubric document."""
    return {
        "format_version": RUBRIC_FORMAT_VERSION,
        "name": name,
        "version": version,
        "title": f"Test taste {version}",
        "dimensions": [
            {"name": "depth", "description": "How deep it goes.",
             "weight": 0.7},
            {"name": "fit", "description": "Watchlist fit.",
             "weight": 0.3, "proxy": "relevance"},
        ],
    }


def _entries(session) -> list[tuple[str, dict]]:
    """The log as (action, detail) pairs, oldest first."""
    recent = LogRepo(session).recent(limit=100)
    return [(e.action, e.detail) for e in reversed(list(recent))]


# -- the shipped default -------------------------------------------------------


def test_shipped_default_rubric_bootstraps_once_idempotently(session):
    ensure_default_rubric_catalog(session)
    ensure_default_rubric_catalog(session)  # second call: no-op
    catalog = RubricCatalog(session)
    doc = catalog.resolve("default")
    assert rubric_ref(doc) == "default@1"
    assert catalog.get("default", 1) == doc
    created = [d for a, d in _entries(session) if a == "rubric_version_created"]
    assert len(created) == 1
    assert created[0] == {"rubric": "default", "version": 1, "via": "shipped"}


def test_bootstrap_leaves_an_existing_row_alone(session):
    catalog = RubricCatalog(session)
    catalog.create_version(base_rubric("default", 1), actor="user")
    ensure_default_rubric_catalog(session)  # present: never touches the row
    assert [v["created_by"] for v in catalog.versions("default")] == ["user"]


# -- versions: dense, immutable, actor-required ---------------------------------


def test_versions_are_dense_and_never_reused(session):
    catalog = RubricCatalog(session)
    assert catalog.create_version(base_rubric(version=1), actor="user") == 1
    assert catalog.create_version(base_rubric(version=2), actor="agent") == 2
    with pytest.raises(CatalogError, match="new version must be 3"):
        catalog.create_version(base_rubric(version=5), actor="user")
    with pytest.raises(CatalogError, match="never overwritten"):
        catalog.create_version(base_rubric(version=2), actor="user")


def test_actor_is_required(session):
    catalog = RubricCatalog(session)
    with pytest.raises(CatalogError, match="actor must be one of"):
        catalog.create_version(base_rubric(), actor="robot")
    catalog.create_version(base_rubric(), actor="user")
    with pytest.raises(CatalogError, match="actor must be one of"):
        catalog.retire("taste", actor="cron")


def test_invalid_documents_never_enter(session):
    catalog = RubricCatalog(session)
    bad = base_rubric()
    bad["dimensions"][0]["weight"] = 0  # a stated preference, not a zero
    with pytest.raises(CatalogError, match="weight must be a number > 0"):
        catalog.create_version(bad, actor="user")
    assert catalog.list() == []


# -- resolution: float / pin / loud failures -------------------------------------


def test_resolve_floats_name_and_pins_version(session):
    catalog = RubricCatalog(session)
    catalog.create_version(base_rubric(version=1), actor="user")
    catalog.create_version(base_rubric(version=2), actor="user")
    assert rubric_ref(catalog.resolve("taste")) == "taste@2"
    assert rubric_ref(catalog.resolve("taste@latest")) == "taste@2"
    assert rubric_ref(catalog.resolve("taste@1")) == "taste@1"
    with pytest.raises(CatalogError, match="has no version 3"):
        catalog.resolve("taste@3")
    with pytest.raises(CatalogError, match="unknown rubric 'ghost'"):
        catalog.resolve("ghost")
    with pytest.raises(CatalogError, match="malformed rubric ref"):
        catalog.resolve("taste@x")


def test_retired_rubric_refuses_resolution_and_versions(session):
    catalog = RubricCatalog(session)
    catalog.create_version(base_rubric(), actor="user")
    catalog.retire("taste", actor="user")
    with pytest.raises(CatalogError, match="retired"):
        catalog.resolve("taste")
    with pytest.raises(CatalogError, match="retired"):
        catalog.create_version(base_rubric(version=2), actor="user")
    catalog.unretire("taste", actor="user")
    assert rubric_ref(catalog.resolve("taste")) == "taste@1"  # data survives
    assert catalog.create_version(base_rubric(version=2), actor="user") == 2


def test_document_inspects_after_retirement(session):
    catalog = RubricCatalog(session)
    catalog.create_version(base_rubric(), actor="user")
    catalog.retire("taste", actor="user")
    assert rubric_ref(catalog.document("taste")) == "taste@1"  # history reads


def test_retire_is_idempotent_and_logged_once(session):
    catalog = RubricCatalog(session)
    catalog.create_version(base_rubric(), actor="user")
    catalog.retire("taste", actor="user")
    catalog.retire("taste", actor="user")
    retired = [d for a, d in _entries(session) if a == "rubric_retired"]
    assert len(retired) == 1


# -- boundary checks on load ------------------------------------------------------


def test_stored_document_is_revalidated_on_load(session, db):
    catalog = RubricCatalog(session)
    catalog.create_version(base_rubric(), actor="user")
    with db.session() as writer:  # a hand-edited SQLite file
        row = writer.get(RubricVersion, ("taste", 1))
        row.document = {**row.document, "dimensions": []}
        writer.commit()
    with pytest.raises(CatalogError, match="changed outside the catalog"):
        catalog.resolve("taste")


def test_row_and_document_must_agree_on_identity(session, db):
    catalog = RubricCatalog(session)
    catalog.create_version(base_rubric(), actor="user")
    with db.session() as writer:
        row = writer.get(RubricVersion, ("taste", 1))
        row.document = {**row.document, "version": 2}
        writer.commit()
    with pytest.raises(CatalogError, match="disagree"):
        catalog.get("taste", 1)


def test_name_row_requires_known_name(session):
    catalog = RubricCatalog(session)
    session.add(Rubric(name="orphan"))  # a name with no versions
    with pytest.raises(CatalogError, match="no versions"):
        catalog.resolve("orphan")
    with pytest.raises(CatalogError, match="unknown rubric"):
        catalog.versions("nope")


# -- the shipped package file the bootstrap loads -----------------------------------


def test_shipped_rubric_loads_validated():
    doc = load_shipped_rubric("default", 1)  # the bootstrap's source
    assert rubric_ref(doc) == "default@1"


# -- diff + size cap (ticket 13/14: the diff core is shared, keyed on dimensions) --


def test_diff_is_order_sensitive_on_dimensions(session):
    """Dimension order is behavior (weights renormalize over the scored
    sequence), so a reorder is one ``order`` entry on ``/dimensions`` —
    the workflow diff's semantics, keyed list parameterized."""
    catalog = RubricCatalog(session)
    catalog.create_version(base_rubric(), actor="user")
    v2 = base_rubric()
    v2["version"] = 2  # same title: the only change is the dimension order
    v2["dimensions"] = list(reversed(v2["dimensions"]))
    catalog.create_version(v2, actor="user")
    changes = catalog.diff("taste", 1, 2)
    assert {(c["path"], c["kind"]) for c in changes} == \
        {("/dimensions", "order"), ("/version", "changed")}
    order = next(c for c in changes if c["kind"] == "order")
    assert order["before"] == ["depth", "fit"]
    assert order["after"] == ["fit", "depth"]

    v3 = base_rubric()
    v3["version"] = 3
    v3["title"] = "Reweighed taste"
    v3["dimensions"][1]["weight"] = 0.9
    catalog.create_version(v3, actor="user")
    assert {(c["path"], c["kind"]) for c in catalog.diff("taste", 1, 3)} == \
        {("/title", "changed"), ("/version", "changed"),
         ("/dimensions/fit/weight", "changed")}

    with pytest.raises(CatalogError, match="one rubric"):
        diff_rubrics(base_rubric("taste"), base_rubric("other"))


def test_oversized_rubric_is_refused_at_save(session):
    catalog = RubricCatalog(session)
    bloated = base_rubric()
    bloated["title"] = "x" * 70_000
    with pytest.raises(CatalogError, match="cap 65536"):
        catalog.create_version(bloated, actor="user")
