"""Plugin registries for the morning pipeline (wayfinder ticket 09).

Registries exist only at points of known variability; each plugin is a deep
module — one narrow call, engine/deployment choices hidden. Selecting a name
that is not registered fails loudly, never as a silent no-op. The notify
registry intentionally ships empty: zero registered implementations is the
defined no-op for the notify stage.
"""

from __future__ import annotations

from typing import Any, Callable


class Registry:
    """Named plugin slots with a declared default and loud failure."""

    def __init__(self, name: str, default: str | None = None):
        self.name = name
        self.default = default
        self._entries: dict[str, Callable[..., Any]] = {}

    def register(self, key: str, entry: Callable[..., Any]) -> None:
        self._entries[key] = entry

    def get(self, key: str | None = None) -> Callable[..., Any]:
        """Resolve ``key`` (or the registry default) to a registered plugin."""
        name = key or self.default
        if name is None or name not in self._entries:
            known = ", ".join(sorted(self._entries)) or "none registered"
            raise KeyError(f"unknown {self.name} plugin '{name}' ({known})")
        return self._entries[name]

    def names(self) -> list[str]:
        return sorted(self._entries)


SCRIPTWRITERS = Registry("SCRIPTWRITERS", default="llm-brief")
TTS_ENGINES = Registry("TTS_ENGINES", default="edge-tts")
PUBLISHERS = Registry("PUBLISHERS", default="cloudflare-pages")
NOTIFIERS = Registry("NOTIFIERS")  # ships empty by design (wayfinder 05/09)


def load_plugins() -> None:
    """Import built-in plugin modules so their registrations run.

    Plugin modules import this package's registries, so they are loaded here
    rather than at module import time to keep imports acyclic.
    """
    from . import scriptwriter  # noqa: F401  (registration side effect)
