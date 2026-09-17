"""Account manager: consent grants + connection checks.

Grants live in ``$NEWSDESK_HOME/accounts.json`` — user-editable metadata
(kind, ref, capability list), never credentials. Connecting an account is
grant-check -> credential resolution -> AccountSession.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..config import Settings
from . import credentials as creds_mod
from .base import AccountError, AccountSession

GRANTS_FILENAME = "accounts.json"

# One entry per shipped provider: fields it needs and the capabilities it
# can be granted. This table is the single source of truth for the CLI.
PROVIDERS: dict[str, dict[str, Any]] = {
    "email": {
        "fields": ["host", "port", "user", "password"],
        "capabilities": ["read_mail"],
        "summary": "IMAP mailbox, read-only (PEEK, UID high-water mark)",
    },
    "twitter": {
        "fields": ["access_token"],
        "capabilities": ["read_timeline"],
        "summary": "X/Twitter home timeline via API v2 (read-only)",
    },
    "youtube-account": {
        "fields": ["api_key", "channel_id"],
        "capabilities": ["read_subscriptions"],
        "summary": "YouTube Data API v3: sync subscriptions to sources",
    },
}


class AccountManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.home / GRANTS_FILENAME

    # -- grants file ------------------------------------------------------

    def grants(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def grant(self, kind: str, ref: str, capabilities: list[str]) -> dict[str, Any]:
        if kind not in PROVIDERS:
            raise AccountError(f"unknown account kind '{kind}' "
                               f"(known: {', '.join(sorted(PROVIDERS))})")
        known = PROVIDERS[kind]["capabilities"]
        unknown = [c for c in capabilities if c not in known]
        if unknown:
            raise AccountError(f"unknown capabilities for {kind}: {', '.join(unknown)} "
                               f"(known: {', '.join(known)})")
        data = self.grants()
        entry = data.get(f"{kind}/{ref}", {})
        merged = sorted(set(entry.get("capabilities", [])) | set(capabilities))
        data[f"{kind}/{ref}"] = {
            "capabilities": merged,
            "granted_at": entry.get("granted_at")
            or datetime.now(timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return data[f"{kind}/{ref}"]

    def revoke(self, kind: str, ref: str) -> bool:
        data = self.grants()
        if f"{kind}/{ref}" not in data:
            return False
        del data[f"{kind}/{ref}"]
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return True

    # -- connection ---------------------------------------------------------

    def connect(self, kind: str, ref: str, capability: str) -> AccountSession:
        if kind not in PROVIDERS:
            raise AccountError(f"unknown account kind '{kind}'")
        entry = self.grants().get(f"{kind}/{ref}")
        if not entry:
            raise AccountError(
                f"no grant for {kind}/{ref}; link it with: "
                f"newsdesk accounts grant {kind} {ref} --cap {capability}"
            )
        capabilities = entry.get("capabilities", [])
        credentials = creds_mod.resolve(kind, ref, PROVIDERS[kind]["fields"])
        session = AccountSession(kind, ref, capabilities, credentials)
        session.require(capability)
        return session

    def status(self) -> list[dict[str, Any]]:
        """Every grant + provider, with credential presence (never values)."""
        rows: list[dict[str, Any]] = []
        grants = self.grants()
        linked = set(grants)
        for key in sorted(grants):
            kind, _, ref = key.partition("/")
            if kind not in PROVIDERS:
                rows.append({"account": key, "kind": kind, "capabilities":
                             grants[key].get("capabilities", []),
                             "credentials": {}, "note": "unknown kind"})
                continue
            rows.append({
                "account": key,
                "kind": kind,
                "capabilities": grants[key].get("capabilities", []),
                "credentials": creds_mod.presence(kind, ref, PROVIDERS[kind]["fields"]),
            })
        for kind in sorted(PROVIDERS):
            default_key = f"{kind}/default"
            if default_key not in linked:
                rows.append({"account": default_key, "kind": kind, "capabilities": [],
                             "credentials": creds_mod.presence(
                                 kind, "default", PROVIDERS[kind]["fields"]),
                             "note": "not linked"})
        return rows
