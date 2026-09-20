"""The workflow descriptor schema, version 1 (wayfinder ticket 01).

A descriptor is the deep interface hiding "what a workflow is": pure data —
an ordered stage list plus workflow-level guardrail config — validated by
this module on save and on load (Cordis: witnesses at definition). Nothing
here executes anything; every check is deterministic, no LLM in any
validator.

Decisions encoded (ticket 01):

- Stage vocabulary is the closed set ``collect, select, compose, render,
  publish, notify``. ``select`` is today's digest (ADR 0001),
  ``compose`` the script-writer, ``render`` the TTS stage.
- Each stage type carries its Cordis triple: what artifacts it requires and
  what it provides on the run's artifact bus. The validator enforces chain
  coherence — every required artifact must be provided by an earlier stage.
- ``plugin`` pins a registry key; absent or null means unpinned (the engine
  resolves it — today via the env-wired settings knobs). The validator does
  not check registration: that is the registry's loud failure at run time.
- Guardrail checks are named members of the closed set below, each with
  params validated here, and an ``on_fail`` policy: ``repair`` (targeted
  stage re-run bounded by ``loop_policy.max_attempts`` — 1..2 — then
  contained degrade, then loud failure) or ``fail`` (loud immediately —
  e.g. archive-intact must never degrade).
- ``format_version`` gates the document shape, like the seed's
  ``format: 1``. Unknown versions are refused, never guessed at.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..morning.script import SECTION_TYPES

FORMAT_VERSION = 1

WORKFLOW_NAME_RE = re.compile(r"[a-z][a-z0-9-]*")
STAGE_NAME_RE = re.compile(r"[a-z][a-z0-9_-]*")

# The Cordis triple per stage type: artifacts required from the bus, and
# provided to it. (The triple's third member, effects, is fixed by the type
# itself — publish is the emission stage, guarded by archive-intact.) One
# triple per type in v1 — all current plugins of a type share it;
# per-plugin overrides arrive with registration metadata (ticket 02).
STAGE_TYPES: dict[str, dict[str, tuple[str, ...]]] = {
    "collect": {"requires": (), "provides": ("collection",)},
    "select": {"requires": ("collection",), "provides": ("digest",)},
    "compose": {"requires": ("digest",), "provides": ("script",)},
    "render": {"requires": ("script",), "provides": ("audio",)},
    "publish": {"requires": ("audio",), "provides": ("episode",)},
    "notify": {"requires": ("episode",), "provides": ()},
}

# The closed set of named deterministic checks: params are validated
# structurally here (the map below is the set's single definition);
# semantics (what the artifact must look like) belong to the engine's
# check library (ticket 02 / W3).
_CHECK_PARAM_KEYS = {
    "archive_intact": set(),
    "distinct_stories": {"min_distinct"},
    "diversity_floor": {"min_themes"},
    "duration_band": {"min_seconds", "max_seconds"},
    "section_allowlist": {"allow"},
    "word_budget": {"per_section", "total"},
}
CHECK_NAMES = tuple(_CHECK_PARAM_KEYS)

ON_FAIL_POLICIES = ("repair", "fail")

_TOP_KEYS = {"format_version", "name", "version", "title", "params",
             "loop_policy", "stages"}
_STAGE_KEYS = {"type", "name", "plugin", "params", "checks"}
_CHECK_KEYS = {"name", "params", "on_fail"}


class DescriptorError(Exception):
    """A descriptor is invalid; message lists every violation found."""


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_params_errors(name: str, params: Any) -> list[str]:
    """Validate one check spec's params against its closed schema."""
    if not isinstance(params, dict):
        return [f"check '{name}': params must be an object"]
    unknown = sorted(set(params) - _CHECK_PARAM_KEYS[name])
    if unknown:
        return [f"check '{name}': unknown params {', '.join(unknown)}"]
    if name == "archive_intact":
        return []
    if name == "distinct_stories":
        return _require_pos_int(params, "min_distinct", name)
    if name == "diversity_floor":
        return _require_pos_int(params, "min_themes", name)
    if name == "duration_band":
        errors = []
        lo, hi = params.get("min_seconds"), params.get("max_seconds")
        if not is_int(lo) or lo < 0:
            errors.append(f"check '{name}': min_seconds must be an integer >= 0")
        if not is_int(hi) or hi < 0:
            errors.append(f"check '{name}': max_seconds must be an integer >= 0")
        if is_int(lo) and is_int(hi) and hi < lo:
            errors.append(f"check '{name}': max_seconds < min_seconds")
        return errors
    if name == "section_allowlist":
        allow = params.get("allow")
        if not isinstance(allow, list) or not allow:
            return [f"check '{name}': allow must be a non-empty list"]
        return _unknown_sections(name, allow)
    if name == "word_budget":
        errors: list[str] = []
        if "per_section" not in params and "total" not in params:
            return [f"check '{name}': needs per_section or total"]
        per = params.get("per_section")
        if "per_section" in params:
            if not isinstance(per, dict) or not per:
                errors.append(f"check '{name}': per_section must be a "
                              f"non-empty object")
            else:
                errors.extend(_unknown_sections(name, per))
                for section, cap in per.items():
                    if not is_int(cap) or cap < 1:
                        errors.append(f"check '{name}': cap for '{section}' "
                                      f"must be an integer >= 1")
        if "total" in params and (not is_int(params["total"])
                                  or params["total"] < 1):
            errors.append(f"check '{name}': total must be an integer >= 1")
        return errors
    return []  # pragma: no cover - every CHECK_NAMES member is handled


def _require_pos_int(params: dict[str, Any], key: str, name: str) -> list[str]:
    value = params.get(key)
    if not is_int(value) or value < 1:
        return [f"check '{name}': {key} must be an integer >= 1"]
    return []


def _stage_label(index: int, stage: Any) -> str:
    """A stage's name for messages: explicit name, else its type."""
    label = stage.get("name") or stage.get("type") or f"#{index}"
    return label if isinstance(label, str) else f"#{index}"


def _unknown_sections(name: str, keys: Any) -> list[str]:
    """Section-type keys outside the vocabulary, for error messages."""
    bad = [t for t in keys if t not in SECTION_TYPES]
    if bad:
        return [f"check '{name}': unknown section types {', '.join(bad)} "
                f"(known: {', '.join(SECTION_TYPES)})"]
    return []


def _stage_errors(index: int, stage: Any) -> list[str]:
    if not isinstance(stage, dict):
        return [f"stage {index}: must be an object"]
    label = _stage_label(index, stage)
    errors: list[str] = []
    unknown = sorted(set(stage) - _STAGE_KEYS)
    if unknown:
        errors.append(f"stage '{label}': unknown keys {', '.join(unknown)}")
    stype = stage.get("type")
    if not isinstance(stype, str) or stype not in STAGE_TYPES:
        errors.append(f"stage '{label}': unknown stage type "
                      f"'{stype}' (known: {', '.join(STAGE_TYPES)})")
    name = stage.get("name")
    if name is not None and not (isinstance(name, str)
                                 and STAGE_NAME_RE.fullmatch(name)):
        errors.append(f"stage '{label}': name must match "
                      f"{STAGE_NAME_RE.pattern}")
    plugin = stage.get("plugin")
    if plugin is not None and not (isinstance(plugin, str) and plugin):
        errors.append(f"stage '{label}': plugin must be a registry key "
                      f"string or null")
    params = stage.get("params")
    if params is not None and not isinstance(params, dict):
        errors.append(f"stage '{label}': params must be an object")
    checks = stage.get("checks")
    if checks is not None:
        if not isinstance(checks, list):
            errors.append(f"stage '{label}': checks must be a list")
        else:
            for check in checks:
                errors.extend(_check_spec_errors(label, check))
    return errors


def _check_spec_errors(stage_label: str, check: Any) -> list[str]:
    if not isinstance(check, dict):
        return [f"stage '{stage_label}': each check must be an object"]
    name = check.get("name")
    if name not in CHECK_NAMES:
        known = ", ".join(CHECK_NAMES)
        return [f"stage '{stage_label}': unknown check '{name}' "
                f"(known: {known})"]
    errors: list[str] = []
    unknown = sorted(set(check) - _CHECK_KEYS)
    if unknown:
        errors.append(f"stage '{stage_label}', check '{name}': unknown keys "
                      f"{', '.join(unknown)}")
    on_fail = check.get("on_fail", "repair")
    if on_fail not in ON_FAIL_POLICIES:
        errors.append(f"stage '{stage_label}', check '{name}': on_fail must "
                      f"be one of {', '.join(ON_FAIL_POLICIES)}")
    errors.extend(
        f"stage '{stage_label}', {e}"
        for e in _check_params_errors(name, check.get("params", {})))
    return errors


def validate_descriptor(doc: Any) -> list[str]:
    """Return every violation of the v1 schema, or an empty list if valid."""
    if not isinstance(doc, dict):
        return ["descriptor must be a JSON object"]
    errors: list[str] = []
    unknown = sorted(set(doc) - _TOP_KEYS)
    if unknown:
        errors.append(f"unknown keys {', '.join(unknown)}")
    version = doc.get("format_version")
    if version != FORMAT_VERSION:
        errors.append(f"format_version must be {FORMAT_VERSION}, "
                      f"got {version!r}")
    name = doc.get("name")
    if not (isinstance(name, str) and WORKFLOW_NAME_RE.fullmatch(name)):
        errors.append(f"name must match {WORKFLOW_NAME_RE.pattern}, "
                      f"got {name!r}")
    rev = doc.get("version")
    if not is_int(rev) or rev < 1:
        errors.append("version must be an integer >= 1")
    title = doc.get("title")
    if title is not None and not isinstance(title, str):
        errors.append("title must be a string")
    params = doc.get("params")
    if params is not None and not isinstance(params, dict):
        errors.append("params must be an object")
    policy = doc.get("loop_policy")
    if policy is not None:
        if not isinstance(policy, dict):
            errors.append("loop_policy must be an object")
        else:
            extra = sorted(set(policy) - {"max_attempts"})
            if extra:
                errors.append(f"loop_policy: unknown keys {', '.join(extra)}")
            attempts = policy.get("max_attempts")
            if attempts is not None and (not is_int(attempts)
                                         or not 1 <= attempts <= 2):
                errors.append("loop_policy: max_attempts must be an "
                              "integer in 1..2 (bounded repair)")

    stages = doc.get("stages")
    if not isinstance(stages, list) or not stages:
        errors.append("stages must be a non-empty list")
    else:
        provided: set[str] = set()
        names: set[str] = set()
        for index, stage in enumerate(stages):
            errors.extend(_stage_errors(index, stage))
            if not isinstance(stage, dict):
                continue
            label = _stage_label(index, stage)
            if label in names:
                errors.append(f"duplicate stage name '{label}' (a stage "
                              f"defaults to its type as name; repeated "
                              f"types need distinct names)")
            names.add(label)
            triple = STAGE_TYPES.get(stage.get("type")) \
                if isinstance(stage.get("type"), str) else None
            if triple:
                for artifact in triple["requires"]:
                    if artifact not in provided:
                        errors.append(
                            f"stage '{label}': requires artifact "
                            f"'{artifact}' that no earlier stage provides")
                provided.update(triple["provides"])
    return errors


def require_valid(doc: Any) -> None:
    """Raise :class:`DescriptorError` listing all violations, if any."""
    errors = validate_descriptor(doc)
    if errors:
        raise DescriptorError("invalid workflow descriptor:\n- "
                              + "\n- ".join(errors))


def validate_registration(stage_type: str, requires: tuple[str, ...],
                          provides: tuple[str, ...]) -> list[str]:
    """Check a plugin's declared triple against its stage type.

    Ticket 01 put per-plugin requires/provides metadata at registration;
    the rule is fixed here (ticket 02): a plugin may narrow its type's
    triple, never contradict it. Lives beside STAGE_TYPES so the
    registries can validate at registration time (the W1 wiring, ticket
    05) without importing the engine.
    """
    triple = STAGE_TYPES.get(stage_type)
    if triple is None:
        return [f"unknown stage type '{stage_type}' "
                f"(known: {', '.join(STAGE_TYPES)})"]
    errors = [f"plugin requires '{key}' which stage type '{stage_type}' "
              f"does not ({', '.join(triple['requires'])})"
              for key in requires if key not in triple["requires"]]
    errors.extend(f"plugin provides '{key}' which stage type "
                  f"'{stage_type}' does not ({', '.join(triple['provides'])})"
                  for key in provides if key not in triple["provides"])
    return errors


def workflow_descriptor_path(name: str, version: int) -> Path:
    """Where a shipped descriptor lives in the package."""
    return Path(__file__).parent / "descriptors" / f"{name}@{version}.json"
