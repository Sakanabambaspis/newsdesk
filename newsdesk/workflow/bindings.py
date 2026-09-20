"""Static binding validation for descriptor documents (wayfinder ticket 13/14).

The engine's pre-flight (``selection``) validates a workflow against the
plugin registries before any stage runs — witnesses at use. The catalog now
validates the same rules at save time (witnesses at definition), so both
call **this one implementation** and cannot drift: a document that could
never run is refused at save, and the engine keeps the final say at run
start (the DB is a boundary channel newsdesk does not exclusively control).

What is checked (pure functions of the document, the registries and the
optional ``defaults`` map — no session, no I/O):

- one strategy per select stage type (two different pins are refused);
- pinned plugins exist in their stage type's registry;
- a pinned select stage carries its required ``rubric`` param;
- stage params stay inside the resolved plugin's closed key set;
- every check is bound at or after the stage that provides the artifacts
  it validates (a check that can never pass must not enter the repair
  loop — the ``CHECK_REQUIRES`` guard).

Settings-blindness is explicit: unpinned ``render``/``publish`` stages
resolve through settings knobs at run start, so ``defaults`` carries those
knobs on the engine path and stays ``None`` on the save path — a save
checks everything statically knowable, the engine re-checks all of it.

Import-order constraint: this module imports ``schema`` and the
registries (whose registrations need ``load_plugins()`` before the checks
run) — never the engine, so the catalog can import it.
"""

from __future__ import annotations

from typing import Any

from ..morning.registries import (PUBLISHERS, SCRIPTWRITERS, TTS_ENGINES,
                                  load_plugins)
from .schema import STAGE_TYPES
from .strategies import SELECT_STRATEGIES

# the registries a document's stage types resolve against (one definition —
# the engine and the catalog pass the same map).
BINDING_REGISTRIES: dict[str, Any] = {
    "compose": SCRIPTWRITERS,
    "render": TTS_ENGINES,
    "publish": PUBLISHERS,
    "select": SELECT_STRATEGIES,
}

# engine built-ins close their own stage-params key sets; registry plugins
# declare theirs at registration (Registry.param_keys)
_BUILTIN_PARAM_KEYS: dict[str, frozenset[str]] = {
    "collect": frozenset(), "select": frozenset({"hours"}),
}

# the bus artifacts each named check validates against (the check library's
# binding guards, known statically). A check bound to a stage that runs
# before its artifacts exist can never pass — refusing that is what keeps
# strategies (and any early stage) out of a doomed repair loop (ticket 08).
CHECK_REQUIRES: dict[str, tuple[str, ...]] = {
    "archive_intact": (),
    "section_allowlist": ("script",),
    "word_budget": ("script",),
    "distinct_stories": ("digest", "script"),
    "diversity_floor": ("digest", "script"),
    "duration_band": ("audio",),
}

_PROVIDERS: dict[str, str] = {artifact: stage_type
                              for stage_type, triple in STAGE_TYPES.items()
                              for artifact in triple["provides"]}


def _stage_param_keys(registry: Any, stype: str, pin: str | None,
                      defaults: dict[str, str]) -> frozenset[str] | None:
    """The closed key set for one stage's resolved plugin, or ``None``
    when the resolution is not static (a settings knob with no default
    supplied — the engine re-checks those at pre-flight)."""
    if pin and registry is not None:  # pinned: the plugin's own key set
        return registry.param_keys(pin)
    if stype in _BUILTIN_PARAM_KEYS:
        return _BUILTIN_PARAM_KEYS[stype]
    if registry is None:  # notify fans out — no plugin, no params at all
        return frozenset()
    if stype == "compose":  # no settings knob: the engine resolves the
        # registry's default, so the save path knows it statically
        return _param_keys_of(registry, registry.default)
    return _param_keys_of(registry, defaults.get(stype))


def _param_keys_of(registry: Any, name: str | None) -> frozenset[str] | None:
    """Resolve one plugin name to its param keys; unknown names are not a
    static error (existence is checked above; the engine's resolution
    raises the run-path message)."""
    if name is None:
        return None
    try:
        resolved, _plugin = registry.resolve(name)
    except KeyError:
        return None
    return registry.param_keys(resolved)


def validate_bindings(descriptor: dict[str, Any],
                      registries: dict[str, Any], *,
                      defaults: dict[str, str] | None = None,
                      check_requires: dict[str, tuple[str, ...]]
                      | None = None) -> list[str]:
    """Every static binding violation of one validated v1 descriptor.

    Returns the (possibly empty) list of human-readable errors; callers
    raise. ``defaults`` maps stage types to the plugin name settings would
    pick for unpinned stages (the engine passes today's knobs; a save
    passes nothing and skips what it cannot know). ``check_requires``
    defaults to :data:`CHECK_REQUIRES` — the engine's check library and
    this map must agree, so tests can pin their sync.
    """
    load_plugins()  # idempotent: the morning plugins must be registered
    defaults = defaults or {}
    requires = CHECK_REQUIRES if check_requires is None else check_requires
    errors: list[str] = []
    stages = descriptor["stages"]

    # one strategy per select stage type: the engine resolves a single
    # strategy per stage type, so two different pins can never run
    select_plugins = {s["plugin"] for s in stages
                      if s["type"] == "select" and s.get("plugin")}
    if len(select_plugins) > 1:
        errors.append(
            "multiple select stages pin different strategies "
            f"({', '.join(sorted(select_plugins))}) — the engine resolves "
            "one strategy per stage type")

    for index, spec in enumerate(stages):
        stype = spec["type"]
        stage = spec.get("name") or stype
        pin = spec.get("plugin")
        registry = registries.get(stype)
        if pin:
            if registry is None:
                errors.append(f"stage '{stage}': stage type '{stype}' "
                              f"takes no plugin pin")
            else:
                try:
                    registry.get(pin)
                except KeyError as exc:
                    errors.append(str(exc.args[0]))
        if stype == "select" and pin:
            ref = (spec.get("params") or {}).get("rubric")
            if not isinstance(ref, str) or not ref.strip():
                errors.append(
                    f"stage '{stage}': select strategy '{pin}' requires a "
                    f"'rubric' param (a rubric ref string, 'name' or "
                    f"'name@version')")
        allowed = _stage_param_keys(registry, stype, pin, defaults)
        if allowed is not None:
            unknown = sorted(set(spec.get("params") or {}) - allowed)
            if unknown:
                errors.append(f"stage '{stage}': unknown params "
                              f"{', '.join(unknown)}")
        for check in spec.get("checks") or []:
            for artifact in requires.get(check["name"], ()):
                provider = _PROVIDERS[artifact]
                if not any(s["type"] == provider
                           for s in stages[:index + 1]):
                    errors.append(
                        f"stage '{stage}', check '{check['name']}': needs "
                        f"the '{artifact}' artifact on the bus — bind it "
                        f"at or after a '{provider}' stage (a check that "
                        f"can never pass must not enter the repair loop)")
    return errors
