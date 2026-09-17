"""Credential resolution: environment first, OS keyring fallback.

Nothing here ever writes to the database, the log, or snapshots. Two env
shapes are supported:

- default account (one per kind):     ``NEWSDESK_EMAIL_PASSWORD``
- explicit account label:             ``NEWSDESK_ACCOUNT__EMAIL__WORK__PASSWORD``

Keyring entries live under service ``newsdesk/<kind>/<ref>`` with one item
per field (username = field name), when the ``keyring`` package is usable.
"""

from __future__ import annotations

import os

from .base import AccountError


def _keyring():
    try:
        import keyring  # optional dependency; absence is not an error
        keyring.get_keyring()  # force backend init to surface a real failure
        return keyring
    except Exception:
        return None


# Env-var prefixes per kind; "youtube-account" reads the friendlier
# NEWSDESK_YOUTUBE_* (keyring services keep the full kind name).
_ENV_KIND = {"youtube-account": "youtube"}


def _env_names(kind: str, ref: str, field: str) -> list[str]:
    kind_upper = _ENV_KIND.get(kind, kind).upper().replace("-", "_")
    field_upper = field.upper()
    names = [f"NEWSDESK_ACCOUNT__{kind_upper}__{ref.upper()}__{field_upper}"]
    if ref == "default":
        names.append(f"NEWSDESK_{kind_upper}_{field_upper}")
    return names


def resolve(kind: str, ref: str, fields: list[str]) -> dict[str, str]:
    """Resolve credential fields. Raises AccountError listing what's missing."""
    ring = _keyring()
    creds: dict[str, str] = {}
    for field in fields:
        value = None
        for name in _env_names(kind, ref, field):
            if os.environ.get(name):
                value = os.environ[name]
                break
        if value is None and ring is not None:
            try:
                value = ring.get_password(f"newsdesk/{kind}/{ref}", field)
            except Exception:
                value = None
        if value:
            creds[field] = value

    missing = [f for f in fields if f not in creds]
    if missing:
        hints = ", ".join(
            name for field in missing for name in _env_names(kind, ref, field)[:1]
        )
        raise AccountError(
            f"missing credentials for {kind}/{ref}: {', '.join(missing)} "
            f"(set e.g. {hints}, or store them in the OS keyring under "
            f"'newsdesk/{kind}/{ref}')"
        )
    return creds


def presence(kind: str, ref: str, fields: list[str]) -> dict[str, bool]:
    """Field -> configured? Presence only; values are never revealed."""
    ring = _keyring()
    out: dict[str, bool] = {}
    for field in fields:
        found = any(os.environ.get(n) for n in _env_names(kind, ref, field))
        if not found and ring is not None:
            try:
                found = ring.get_password(f"newsdesk/{kind}/{ref}", field) is not None
            except Exception:
                pass
        out[field] = found
    return out
