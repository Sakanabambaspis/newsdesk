"""Plugin registries for the morning pipeline (wayfinder ticket 09).

Registries exist only at points of known variability; each plugin is a deep
module — one narrow call, engine/deployment choices hidden. Selecting a name
that is not registered fails loudly, never as a silent no-op. The notify
registry intentionally ships empty: zero registered implementations is the
defined no-op for the notify stage.

Registration metadata (workflow tickets 01/02/05): a plugin may declare the
stage type it serves; its Cordis triple is then validated against that type
at registration — a plugin may narrow the type's triple, never contradict
it, and a contradiction is a loud error at definition, not a run-time
surprise. Registration also closes the plugin's stage-params key set (the
engine rejects unknown keys at pre-flight) and a ``context=True`` flag,
which marks a context-native plugin: the engine passes the RunContext so
the plugin can read repair bookkeeping (``violations``, ``degrade``) and
vary its output on bounded re-runs.
"""

from __future__ import annotations

from typing import Any, Callable

from ..workflow.schema import STAGE_TYPES, validate_registration


class Registry:
    """Named plugin slots with a declared default and loud failure."""

    def __init__(self, name: str, default: str | None = None):
        self.name = name
        self.default = default
        self._entries: dict[str, Callable[..., Any]] = {}
        self._meta: dict[str, dict[str, Any]] = {}

    def register(self, key: str, entry: Callable[..., Any], *,
                 stage: str | None = None,
                 requires: tuple[str, ...] | None = None,
                 provides: tuple[str, ...] | None = None,
                 params: tuple[str, ...] | None = None,
                 context: bool = False) -> None:
        """Register ``key`` with its declared metadata (validated now —
        witnesses at definition).

        ``stage`` attaches Cordis metadata: the plugin's triple is
        validated against the stage type (``requires``/``provides`` may
        only narrow it) and ``params`` closes the stage-params key set
        the engine enforces at pre-flight. ``context=True`` marks a
        context-native plugin: the engine passes the RunContext so the
        plugin can read the repair bookkeeping.
        """
        if stage is not None:
            if stage not in STAGE_TYPES:
                raise ValueError(
                    f"plugin '{key}': unknown stage type '{stage}' "
                    f"(known: {', '.join(STAGE_TYPES)})")
            triple = STAGE_TYPES[stage]
            errors = validate_registration(
                stage, requires if requires is not None else triple["requires"],
                provides if provides is not None else triple["provides"])
            if errors:
                raise ValueError(f"plugin '{key}' contradicts stage type "
                                 f"'{stage}': " + "; ".join(errors))
        self._entries[key] = entry
        self._meta[key] = {"context": context,
                           "params": frozenset(params or ())}

    def get(self, key: str | None = None) -> Callable[..., Any]:
        """Resolve ``key`` (or the registry default) to a registered plugin."""
        name = key or self.default
        if name is None or name not in self._entries:
            known = ", ".join(sorted(self._entries)) or "none registered"
            raise KeyError(f"unknown {self.name} plugin '{name}' ({known})")
        return self._entries[name]

    def resolve(self, key: str | None = None) -> tuple[str, Callable[..., Any]]:
        """Resolve ``key`` (or the registry default) to ``(name, plugin)``
        — the engine's log-and-metadata form."""
        name = key or self.default
        return name, self.get(name)

    def names(self) -> list[str]:
        return sorted(self._entries)

    def is_context_native(self, key: str) -> bool:
        """Whether the engine passes the RunContext to this plugin."""
        return bool(self._meta.get(key, {}).get("context"))

    def param_keys(self, key: str) -> frozenset[str]:
        """The closed set of stage-param keys this plugin accepts."""
        return self._meta.get(key, {}).get("params") or frozenset()


SCRIPTWRITERS = Registry("SCRIPTWRITERS", default="llm-brief")
TTS_ENGINES = Registry("TTS_ENGINES", default="edge-tts")
PUBLISHERS = Registry("PUBLISHERS", default="cloudflare-pages")
NOTIFIERS = Registry("NOTIFIERS")  # ships empty by design (wayfinder 05/09)


def load_plugins() -> None:
    """Import built-in plugin modules so their registrations run.

    Plugin modules import this package's registries, so they are loaded here
    rather than at module import time to keep imports acyclic.
    """
    from . import cloudflare  # noqa: F401  (registration side effect)
    from . import edgetts  # noqa: F401  (registration side effect)
    from . import publish  # noqa: F401  (registration side effect)
    from . import scriptwriter  # noqa: F401  (registration side effect)
