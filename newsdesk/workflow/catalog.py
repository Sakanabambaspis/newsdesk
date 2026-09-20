"""The workflow catalog: versioned storage for descriptor documents (W2).

Module 4 of the workflow architecture (wayfinder tickets 04 + 06). The
semantics were fixed by ticket 04; this module implements exactly that
surface:

- Identity is ``name@version`` with *dense* integer versions — the next
  version is exactly ``max + 1`` (gaps only ever signal a corrupt
  seed/DB) and a version is never reused or overwritten.
- ``latest`` is computed, never stored: ``name`` and ``name@latest``
  float at run start, ``name@N`` pins. Reproducibility lives in the log
  (every engine event records the version that ran), not in a pointer.
- ``workflow_versions`` is INSERT-only — no update or delete exists;
  retirement is a name-level flag on ``workflows`` (retire-never-
  delete; delete is not expressible in this API at all).
- Every mutation takes a *required* ``actor`` (``system | user |
  agent``) and lands in the log; the seed/shipped paths add ``via`` to
  the log detail so origin is auditable without new columns.
- A retired name refuses new versions and resolution — loudly
  (``CatalogError``), never a silent skip or a silent fallback.
- Documents are validated on save AND on load: the DB is a boundary
  channel newsdesk does not exclusively control (a hand-edited SQLite
  file, a restored backup), and failing loud beats silently running a
  wrong document.
- The engine stays catalog-blind: ``resolve(ref)`` is the only
  resolution path and returns a validated document copy.

Import-order constraint: like the rest of this package, this module
imports ``schema`` only — never the engine.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from sqlmodel import Session, col, select

from ..core.models import (ACTORS, Workflow, WorkflowVersion, iso_utc,
                           utcnow)
from ..storage.repo import LogRepo
from .schema import DescriptorError, require_valid, workflow_descriptor_path


class CatalogError(Exception):
    """A catalog operation failed; message is safe to print (no secrets)."""


def parse_ref(ref: str, *, kind: str = "workflow") -> tuple[str, int | None]:
    """Split a catalog ref into ``(name, version)``.

    ``name`` and ``name@latest`` float (version ``None``), ``name@N``
    pins. Names cannot contain ``@`` (the schema's name pattern), so the
    split is unambiguous; malformed refs raise :class:`CatalogError`.
    ``kind`` names the catalog in error messages (workflow/rubric).
    """
    if not isinstance(ref, str) or not ref.strip():
        raise CatalogError(f"{kind} ref must be a non-empty string")
    name, sep, tail = ref.rpartition("@")
    if not sep:
        return ref, None
    if not name:
        raise CatalogError(f"malformed {kind} ref '{ref}'")
    if tail == "latest":
        return name, None
    try:
        return name, int(tail)
    except ValueError:
        raise CatalogError(f"malformed {kind} ref '{ref}' (version must "
                           f"be an integer or 'latest')") from None


# -- the structural diff (module-level and pure; CLI and agent tools are
#    thin renderers of the same JSON) ------------------------------------------

def _pointer(token: str) -> str:
    """RFC 6901 JSON-pointer token escape."""
    return token.replace("~", "~0").replace("/", "~1")


def _stage_label(stage: dict[str, Any]) -> str:
    """A stage's identity: its explicit name, else its type — the same
    label the engine uses for report keys and log events."""
    return stage.get("name") or stage["type"]


def diff_descriptors(a: Any, b: Any) -> list[dict[str, Any]]:
    """Structural diff of two v1 descriptors (ticket 04).

    Returns ``{path, kind, before, after}`` entries with RFC 6901
    JSON-pointer paths and ``kind`` in ``added | removed | changed |
    order``. Stages compare keyed by stage name (a mid-list insert does
    not cascade "changed" onto every later stage); any change in the
    stage sequence is one ``order`` entry on ``/stages`` — order is
    meaningful, the interpreter is linear. Both documents must be valid
    v1 and share the workflow name.
    """
    require_valid(a)
    require_valid(b)
    if a["name"] != b["name"]:
        raise CatalogError(f"cannot diff '{a['name']}' against "
                           f"'{b['name']}' — diffs compare versions of "
                           f"one workflow")
    out: list[dict[str, Any]] = []
    _diff_node("", a, b, out)
    return out


def _diff_node(path: str, va: Any, vb: Any,
               out: list[dict[str, Any]]) -> None:
    if va == vb:  # parsed equality: structure, not bytes
        return
    if isinstance(va, dict) and isinstance(vb, dict):
        for key in sorted(set(va) | set(vb)):
            token = f"{path}/{_pointer(str(key))}"
            if key not in va:
                out.append({"path": token, "kind": "added",
                            "before": None, "after": vb[key]})
            elif key not in vb:
                out.append({"path": token, "kind": "removed",
                            "before": va[key], "after": None})
            else:
                _diff_node(token, va[key], vb[key], out)
    elif isinstance(va, list) and isinstance(vb, list):
        if path == "/stages":
            _diff_stages(va, vb, out)
            return
        for i in range(max(len(va), len(vb))):
            token = f"{path}/{i}"
            if i >= len(va):
                out.append({"path": token, "kind": "added",
                            "before": None, "after": vb[i]})
            elif i >= len(vb):
                out.append({"path": token, "kind": "removed",
                            "before": va[i], "after": None})
            else:
                _diff_node(token, va[i], vb[i], out)
    else:
        out.append({"path": path or "/", "kind": "changed",
                    "before": va, "after": vb})


def _diff_stages(a: list[Any], b: list[Any], out: list[dict[str, Any]]) -> None:
    names_a = [_stage_label(s) for s in a]
    names_b = [_stage_label(s) for s in b]
    by_a = {_stage_label(s): s for s in a}
    by_b = {_stage_label(s): s for s in b}
    if names_a != names_b:
        out.append({"path": "/stages", "kind": "order",
                    "before": names_a, "after": names_b})
    for label in dict.fromkeys(names_a + names_b):  # order-stable union
        token = f"/stages/{_pointer(label)}"
        if label not in by_a:
            out.append({"path": token, "kind": "added",
                        "before": None, "after": by_b[label]})
        elif label not in by_b:
            out.append({"path": token, "kind": "removed",
                        "before": by_a[label], "after": None})
        else:
            _diff_node(token, by_a[label], by_b[label], out)


# -- the repo ------------------------------------------------------------------

def _require_actor(actor: str) -> None:
    if actor not in ACTORS:
        raise CatalogError(f"actor must be one of {', '.join(ACTORS)}, "
                           f"got {actor!r}")


def _detail(name: str, via: str | None,
            version: int | None = None) -> dict[str, Any]:
    detail: dict[str, Any] = {"workflow": name}
    if version is not None:
        detail["version"] = version
    if via:
        detail["via"] = via
    return detail


def _validated_document(row: WorkflowVersion) -> dict[str, Any]:
    """Load-time boundary check (ticket 04): re-validate the stored
    document, match its identity against the row, and hand back a private
    copy — callers must never alias the row's JSON column."""
    doc = row.document
    try:
        require_valid(doc)
    except DescriptorError as exc:
        raise CatalogError(
            f"stored workflow '{row.name}@{row.version}' is invalid — the "
            f"storage channel was changed outside the catalog: "
            f"{exc}") from exc
    if doc["name"] != row.name or doc["version"] != row.version:
        raise CatalogError(
            f"stored workflow '{row.name}@{row.version}' carries identity "
            f"'{doc['name']}@{doc['version']}' — the row and its document "
            f"disagree")
    return deepcopy(doc)


class WorkflowCatalog:
    """Repo-layer catalog over ``workflows`` + append-only
    ``workflow_versions`` (the LogRepo idiom: only this layer talks to
    the tables, and only through operations the semantics allow)."""

    def __init__(self, session: Session):
        self.session = session

    # -- reads

    def get(self, name: str, version: int) -> dict[str, Any] | None:
        """One stored document, re-validated on load. Missing versions
        return ``None`` — loud resolution failures are ``resolve``'s job;
        inspection of history stays possible (even when retired)."""
        row = self._version_row(name, version)
        return None if row is None else _validated_document(row)

    def versions(self, name: str) -> list[dict[str, Any]]:
        """Full history of one name, ascending; unknown names are loud.
        Every document is re-validated on the way out (boundary check)."""
        if self._name_row(name) is None:
            raise CatalogError(f"unknown workflow '{name}'")
        rows = self.session.exec(
            select(WorkflowVersion).where(WorkflowVersion.name == name)
            .order_by(col(WorkflowVersion.version))).all()
        return [{"version": r.version, "created_by": r.created_by,
                 "created_at": iso_utc(r.created_at),
                 "document": _validated_document(r)} for r in rows]

    def list(self) -> list[dict[str, Any]]:
        """Every name with its computed latest version and retirement,
        ordered by name."""
        out: list[dict[str, Any]] = []
        for row in self.session.exec(
                select(Workflow).order_by(col(Workflow.name))).all():
            out.append({"name": row.name,
                        "created_at": iso_utc(row.created_at),
                        "retired_at": iso_utc(row.retired_at),
                        "latest": self._max_version(row.name)})
        return out

    def resolve(self, ref: str) -> dict[str, Any]:
        """The only resolution path: float ``name``/``name@latest`` or pin
        ``name@N``, returned as a validated document copy. Unknown,
        retired, malformed refs and invalid stored documents all fail
        loudly — never a silent skip, never a silent fallback to an older
        version."""
        name, version = parse_ref(ref)
        name_row = self._name_row(name)
        if name_row is None:
            raise CatalogError(f"unknown workflow '{name}'")
        if name_row.retired_at is not None:
            raise CatalogError(
                f"workflow '{name}' is retired (since "
                f"{iso_utc(name_row.retired_at)}) — un-retire it or "
                f"rebind what references it")
        return _validated_document(self._row_for_ref(name, version))

    def document(self, ref: str) -> dict[str, Any]:
        """One stored document by ref, for *inspection*: like
        ``resolve`` but retirement does not refuse — history stays
        readable after a retire (the fix for a retired workflow is
        deliberate rebinding, not amnesia)."""
        name, version = parse_ref(ref)
        self._require_name(name)
        return _validated_document(self._row_for_ref(name, version))

    def diff(self, name: str, version_a: int,
             version_b: int) -> list[dict[str, Any]]:
        """Structural diff between two stored versions — a thin lookup
        over the module-level :func:`diff_descriptors`."""
        a, b = self.get(name, version_a), self.get(name, version_b)
        if a is None:
            raise CatalogError(f"workflow '{name}' has no version "
                               f"{version_a}")
        if b is None:
            raise CatalogError(f"workflow '{name}' has no version "
                               f"{version_b}")
        return diff_descriptors(a, b)

    # -- writes (every one: required actor, logged)

    def create_version(self, doc: dict[str, Any], actor: str, *,
                       via: str | None = None) -> int:
        """Append one version; the name row is created implicitly by the
        first version (one entry point; ``workflow_created`` vs
        ``workflow_version_created`` distinguish it in the log).

        The document must be valid v1 — nothing invalid ever enters —
        and its embedded ``name``/``version`` are the row's identity.
        The version must be exactly ``max + 1`` (or 1 for a new name)
        and must not already exist: dense, never reused, never
        overwritten. Returns the stored version.
        """
        _require_actor(actor)
        try:
            require_valid(doc)
        except DescriptorError as exc:
            raise CatalogError(str(exc)) from exc
        name, version = doc["name"], doc["version"]
        name_row = self._name_row(name)
        if name_row is not None and name_row.retired_at is not None:
            raise CatalogError(
                f"workflow '{name}' is retired (since "
                f"{iso_utc(name_row.retired_at)}) — un-retire it before "
                f"adding versions")
        if self._version_row(name, version) is not None:
            raise CatalogError(
                f"workflow '{name}' version {version} already exists — "
                f"versions are immutable and never overwritten")
        expected = 1 if name_row is None else self._max_version(name) + 1
        if version != expected:
            raise CatalogError(
                f"workflow '{name}': new version must be {expected} "
                f"(dense: next is max+1), got {version}")
        if name_row is None:
            self.session.add(Workflow(name=name))
        self.session.add(WorkflowVersion(
            name=name, version=version, document=deepcopy(doc),
            created_by=actor))
        self.session.commit()
        if name_row is None:
            self._log("workflow_created", _detail(name, via), actor)
        self._log("workflow_version_created",
                  _detail(name, via, version=version), actor)
        return version

    def retire(self, name: str, actor: str, *,
               via: str | None = None) -> None:
        """Flag the name retired: new versions and resolution are refused
        from now on, history stays untouched. Idempotent — retiring a
        retired name is a no-op, not a log entry."""
        _require_actor(actor)
        row = self._require_name(name)
        if row.retired_at is not None:
            return
        row.retired_at = utcnow()
        self.session.add(row)
        self.session.commit()
        self._log("workflow_retired", _detail(name, via), actor)

    def unretire(self, name: str, actor: str, *,
                 via: str | None = None) -> None:
        """Clear the retirement flag (the data was never gone). Logged;
        idempotent like ``retire``."""
        _require_actor(actor)
        row = self._require_name(name)
        if row.retired_at is None:
            return
        row.retired_at = None
        self.session.add(row)
        self.session.commit()
        self._log("workflow_unretired", _detail(name, via), actor)

    # -- internals

    def _log(self, action: str, detail: dict[str, Any], actor: str) -> None:
        LogRepo(self.session).append(action, detail, actor=actor)

    def _require_name(self, name: str) -> Workflow:
        row = self._name_row(name)
        if row is None:
            raise CatalogError(f"unknown workflow '{name}'")
        return row

    def _name_row(self, name: str) -> Workflow | None:
        return self.session.get(Workflow, name)

    def _version_row(self, name: str,
                     version: int) -> WorkflowVersion | None:
        return self.session.exec(
            select(WorkflowVersion).where(WorkflowVersion.name == name,
                                          WorkflowVersion.version == version)
        ).first()

    def _latest_row(self, name: str) -> WorkflowVersion | None:
        return self.session.exec(
            select(WorkflowVersion).where(WorkflowVersion.name == name)
            .order_by(col(WorkflowVersion.version).desc())
        ).first()

    def _row_for_ref(self, name: str, version: int | None) -> WorkflowVersion:
        """Float to the computed latest or fetch the pinned version;
        missing versions are loud."""
        if version is None:
            row = self._latest_row(name)
            if row is None:
                raise CatalogError(f"workflow '{name}' has no versions")
            return row
        row = self._version_row(name, version)
        if row is None:
            raise CatalogError(
                f"workflow '{name}' has no version {version} "
                f"(latest: {self._max_version(name)})")
        return row

    def _max_version(self, name: str) -> int | None:
        row = self._latest_row(name)
        return None if row is None else row.version


DEFAULT_WORKFLOW = "default-morning"
DEFAULT_VERSION = 1


def ensure_default_catalog(session: Session) -> None:
    """Import the shipped ``default-morning@1`` once, idempotently
    (actor=system, via=shipped — ticket 06).

    After the import the DB row is the living data and the package file
    freezes: user edits create DB version 2 and this never touches the
    row again — an existing (or retired) ``default-morning@1`` is left
    exactly as the database has it.
    """
    catalog = WorkflowCatalog(session)
    if catalog.get(DEFAULT_WORKFLOW, DEFAULT_VERSION) is not None:
        return
    path = workflow_descriptor_path(DEFAULT_WORKFLOW, DEFAULT_VERSION)
    doc = json.loads(path.read_text(encoding="utf-8"))
    catalog.create_version(doc, actor="system", via="shipped")
