"""The rubric catalog: versioned storage for rubric documents (W3).

Module 5's storage half (wayfinder tickets 07 + 09): the selection
strategies name a rubric by ref string, and this module is the only
resolution path. The semantics are exactly the workflow catalog's
(ticket 04), so the same rules hold here:

- Identity is ``name@version`` with *dense* integer versions — the next
  version is exactly ``max + 1`` and a version is never reused or
  overwritten. ``latest`` is computed, never stored: ``name`` and
  ``name@latest`` float at run start, ``name@N`` pins.
- ``rubric_versions`` is INSERT-only; retirement is a name-level flag on
  ``rubrics`` (retire-never-delete). A retired name refuses new versions
  and resolution — loudly, never a silent fallback.
- Every mutation takes a *required* ``actor`` and lands in the log; the
  shipped-bootstrap path adds ``via`` to the detail.
- Documents are validated on save AND on load (the DB is a boundary
  channel), and a stored document's identity must agree with its row.

What is deliberately absent: the seed sections (rubrics enter through
agent tools, tickets 13/14). The structural diff exists since ticket 14:
``diff_rubrics`` shares the workflow diff's core with the order-sensitive
keyed list parameterized to ``dimensions`` (reordering dimensions changes
the weighted mean's renormalization inputs — order is behavior there too).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlmodel import Session, col, select

from ..core.models import ACTORS, Rubric, RubricVersion, iso_utc, utcnow
from ..storage.repo import LogRepo
from .catalog import (CatalogError, require_document_size, diff_documents,
                      parse_ref)
from .rubric import (RubricError, load_shipped_rubric, require_valid_rubric,
                     rubric_ref)


def _dimension_label(dimension: dict[str, Any]) -> str:
    """A dimension's identity: its name (the schema requires one)."""
    return dimension["name"]


def diff_rubrics(a: Any, b: Any) -> list[dict[str, Any]]:
    """Structural diff of two rubric documents (ticket 13): the workflow
    diff's shape, keyed by dimension name with any sequence change as one
    ``order`` entry on ``/dimensions``. Both documents must be valid and
    share the rubric name."""
    require_valid_rubric(a)
    require_valid_rubric(b)
    if a["name"] != b["name"]:
        raise CatalogError(f"cannot diff '{a['name']}' against "
                           f"'{b['name']}' — diffs compare versions of "
                           f"one rubric")
    return diff_documents(a, b, list_key="dimensions",
                          label_of=_dimension_label)


def _validated_document(row: RubricVersion) -> dict[str, Any]:
    """Load-time boundary check: re-validate the stored document, match
    its identity against the row, and hand back a private copy — callers
    must never alias the row's JSON column."""
    doc = row.document
    try:
        require_valid_rubric(doc)
    except RubricError as exc:
        raise CatalogError(
            f"stored rubric '{row.name}@{row.version}' is invalid — the "
            f"storage channel was changed outside the catalog: "
            f"{exc}") from exc
    if doc["name"] != row.name or doc["version"] != row.version:
        raise CatalogError(
            f"stored rubric '{row.name}@{row.version}' carries identity "
            f"'{doc['name']}@{doc['version']}' — the row and its document "
            f"disagree")
    return deepcopy(doc)


class RubricCatalog:
    """Repo-layer catalog over ``rubrics`` + append-only
    ``rubric_versions`` (the LogRepo idiom: only this layer talks to the
    tables, and only through operations the semantics allow)."""

    def __init__(self, session: Session):
        self.session = session

    # -- reads

    def get(self, name: str, version: int) -> dict[str, Any] | None:
        """One stored document, re-validated on load; missing versions
        return ``None`` — loud resolution is ``resolve``'s job."""
        row = self._version_row(name, version)
        return None if row is None else _validated_document(row)

    def versions(self, name: str) -> list[dict[str, Any]]:
        """Full history of one name, ascending; unknown names are loud."""
        if self._name_row(name) is None:
            raise CatalogError(f"unknown rubric '{name}'")
        rows = self.session.exec(
            select(RubricVersion).where(RubricVersion.name == name)
            .order_by(col(RubricVersion.version))).all()
        return [{"version": r.version, "created_by": r.created_by,
                 "created_at": iso_utc(r.created_at),
                 "document": _validated_document(r)} for r in rows]

    def list(self) -> list[dict[str, Any]]:
        """Every name with its computed latest version and retirement,
        ordered by name."""
        return [{"name": row.name,
                 "created_at": iso_utc(row.created_at),
                 "retired_at": iso_utc(row.retired_at),
                 "latest": self._max_version(row.name)}
                for row in self.session.exec(
                    select(Rubric).order_by(col(Rubric.name))).all()]

    def resolve(self, ref: str) -> dict[str, Any]:
        """The only resolution path: float ``name``/``name@latest`` or pin
        ``name@N``, returned as a validated document copy. Unknown,
        retired, malformed refs and invalid stored documents all fail
        loudly — a select stage never scores against a silent fallback."""
        name, version = parse_ref(ref, kind="rubric")
        name_row = self._name_row(name)
        if name_row is None:
            raise CatalogError(f"unknown rubric '{name}'")
        if name_row.retired_at is not None:
            raise CatalogError(
                f"rubric '{name}' is retired (since "
                f"{iso_utc(name_row.retired_at)}) — un-retire it or "
                f"rebind what references it")
        return _validated_document(self._row_for_ref(name, version))

    def document(self, ref: str) -> dict[str, Any]:
        """One stored document by ref, for *inspection*: like
        ``resolve`` but retirement does not refuse — history stays
        readable after a retire."""
        name, version = parse_ref(ref, kind="rubric")
        self._require_name(name)
        return _validated_document(self._row_for_ref(name, version))

    def diff(self, name: str, version_a: int,
             version_b: int) -> list[dict[str, Any]]:
        """Structural diff between two stored versions — a thin lookup
        over the module-level :func:`diff_rubrics` (order-sensitive on
        ``dimensions``, ticket 13)."""
        a, b = self.get(name, version_a), self.get(name, version_b)
        if a is None:
            raise CatalogError(f"rubric '{name}' has no version "
                               f"{version_a}")
        if b is None:
            raise CatalogError(f"rubric '{name}' has no version "
                               f"{version_b}")
        return diff_rubrics(a, b)

    # -- writes (every one: required actor, logged)

    def create_version(self, doc: dict[str, Any], actor: str, *,
                       via: str | None = None) -> int:
        """Append one version; the name row is created implicitly by the
        first version. The document must be valid, self-consistent with
        the row identity, and exactly ``max + 1`` — dense, never reused,
        never overwritten."""
        if actor not in ACTORS:
            raise CatalogError(f"actor must be one of {', '.join(ACTORS)}, "
                               f"got {actor!r}")
        try:
            require_valid_rubric(doc)
        except RubricError as exc:
            raise CatalogError(str(exc)) from exc
        require_document_size(doc, name=doc.get("name"))
        name, version = doc["name"], doc["version"]
        name_row = self._name_row(name)
        if name_row is not None and name_row.retired_at is not None:
            raise CatalogError(
                f"rubric '{name}' is retired (since "
                f"{iso_utc(name_row.retired_at)}) — un-retire it before "
                f"adding versions")
        if self._version_row(name, version) is not None:
            raise CatalogError(
                f"rubric '{name}' version {version} already exists — "
                f"versions are immutable and never overwritten")
        expected = 1 if name_row is None else self._max_version(name) + 1
        if version != expected:
            raise CatalogError(
                f"rubric '{name}': new version must be {expected} "
                f"(dense: next is max+1), got {version}")
        if name_row is None:
            self.session.add(Rubric(name=name))
        self.session.add(RubricVersion(
            name=name, version=version, document=deepcopy(doc),
            created_by=actor))
        self.session.commit()
        detail: dict[str, Any] = {"rubric": name}
        if via:
            detail["via"] = via
        if name_row is None:
            self._log("rubric_created", dict(detail), actor)
        self._log("rubric_version_created", {**detail, "version": version},
                  actor)
        return version

    def retire(self, name: str, actor: str, *, via: str | None = None) -> None:
        """Flag the name retired: new versions and resolution are refused
        from now on, history stays untouched. Idempotent."""
        if actor not in ACTORS:
            raise CatalogError(f"actor must be one of {', '.join(ACTORS)}, "
                               f"got {actor!r}")
        row = self._require_name(name)
        if row.retired_at is not None:
            return
        row.retired_at = utcnow()
        self.session.add(row)
        self.session.commit()
        detail: dict[str, Any] = {"rubric": name}
        if via:
            detail["via"] = via
        self._log("rubric_retired", detail, actor)

    def unretire(self, name: str, actor: str, *,
                 via: str | None = None) -> None:
        """Clear the retirement flag (the data was never gone). Logged;
        idempotent like ``retire``."""
        if actor not in ACTORS:
            raise CatalogError(f"actor must be one of {', '.join(ACTORS)}, "
                               f"got {actor!r}")
        row = self._require_name(name)
        if row.retired_at is None:
            return
        row.retired_at = None
        self.session.add(row)
        self.session.commit()
        detail: dict[str, Any] = {"rubric": name}
        if via:
            detail["via"] = via
        self._log("rubric_unretired", detail, actor)

    # -- internals

    def _log(self, action: str, detail: dict[str, Any], actor: str) -> None:
        LogRepo(self.session).append(action, detail, actor=actor)

    def _require_name(self, name: str) -> Rubric:
        row = self._name_row(name)
        if row is None:
            raise CatalogError(f"unknown rubric '{name}'")
        return row

    def _name_row(self, name: str) -> Rubric | None:
        return self.session.get(Rubric, name)

    def _version_row(self, name: str,
                     version: int) -> RubricVersion | None:
        return self.session.exec(
            select(RubricVersion).where(RubricVersion.name == name,
                                        RubricVersion.version == version)
        ).first()

    def _latest_row(self, name: str) -> RubricVersion | None:
        return self.session.exec(
            select(RubricVersion).where(RubricVersion.name == name)
            .order_by(col(RubricVersion.version).desc())).first()

    def _row_for_ref(self, name: str, version: int | None) -> RubricVersion:
        """Float to the computed latest or fetch the pinned version;
        missing versions are loud."""
        if version is None:
            row = self._latest_row(name)
            if row is None:
                raise CatalogError(f"rubric '{name}' has no versions")
            return row
        row = self._version_row(name, version)
        if row is None:
            raise CatalogError(
                f"rubric '{name}' has no version {version} "
                f"(latest: {self._max_version(name)})")
        return row

    def _max_version(self, name: str) -> int | None:
        row = self._latest_row(name)
        return None if row is None else row.version


DEFAULT_RUBRIC = "default"
DEFAULT_RUBRIC_VERSION = 1


def ensure_default_rubric_catalog(session: Session) -> None:
    """Import the shipped ``default@1`` rubric once, idempotently
    (actor=system, via=shipped) — the rubric-side twin of the workflow
    catalog's bootstrap. After the import the DB row is the living data
    and the package file freezes.
    """
    catalog = RubricCatalog(session)
    if catalog.get(DEFAULT_RUBRIC, DEFAULT_RUBRIC_VERSION) is not None:
        return
    catalog.create_version(load_shipped_rubric(DEFAULT_RUBRIC,
                                               DEFAULT_RUBRIC_VERSION),
                           actor="system", via="shipped")


__all__ = ["RubricCatalog", "CatalogError", "rubric_ref",
           "ensure_default_rubric_catalog"]
