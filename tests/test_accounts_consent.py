"""Characterization tests for the account consent chain (accounts/base,
manager, credentials) — the §18 discipline: grants are capability lists,
credentials resolve per-run from env/keyring and never persist, and the
fetcher base class enforces grant -> credentials -> capability in order.
All offline (env vars via monkeypatch; no keyring; no IMAP/HTTP).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from newsdesk.accounts.base import (AccountError, AccountFetcher,
                                    AccountSession, parse_account_ref)
from newsdesk.accounts.manager import AccountManager
from newsdesk.config import Settings

_NOW = datetime.now(timezone.utc)


# -- grants file ----------------------------------------------------------------


def test_grant_writes_capabilities_only_never_credentials(settings):
    mgr = AccountManager(settings)
    entry = mgr.grant("email", "main", ["read_mail"])
    assert entry == {"capabilities": ["read_mail"], "granted_at": entry["granted_at"]}
    data = json.loads((settings.home / "accounts.json").read_text())
    assert data == {"email/main": entry}
    assert "password" not in json.dumps(data).lower()


def test_grant_rejects_unknown_kind_and_unknown_capabilities(settings):
    mgr = AccountManager(settings)
    with pytest.raises(AccountError, match="unknown account kind 'gopher'"):
        mgr.grant("gopher", "main", ["read_mail"])
    mgr.grant("email", "main", ["read_mail"])
    with pytest.raises(AccountError, match="unknown capabilities for email"):
        mgr.grant("email", "main", ["send_mail", "read_mail"])


def test_grant_merges_capabilities_and_preserves_original_granted_at(settings):
    mgr = AccountManager(settings)
    first = mgr.grant("email", "main", ["read_mail"])
    second = mgr.grant("email", "main", ["read_mail"])  # idempotent merge
    assert second["capabilities"] == ["read_mail"]
    assert second["granted_at"] == first["granted_at"]


def test_revoke_removes_entry_and_reports_missing(settings):
    mgr = AccountManager(settings)
    mgr.grant("email", "main", ["read_mail"])
    assert mgr.revoke("email", "main") is True
    assert mgr.revoke("email", "main") is False
    assert mgr.grants() == {}


def test_status_lists_linked_and_unlinked_defaults(settings):
    mgr = AccountManager(settings)
    rows = {r["account"]: r for r in mgr.status()}
    assert set(rows) == {"email/default", "twitter/default",
                         "youtube-account/default"}
    assert all(r["note"] == "not linked" for r in rows.values())
    assert all(isinstance(v, bool) for r in rows.values()
               for v in r["credentials"].values())  # presence, never values

    mgr.grant("twitter", "news", ["read_timeline"])
    rows = {r["account"]: r for r in mgr.status()}
    assert rows["twitter/news"]["capabilities"] == ["read_timeline"]
    assert "note" not in rows["twitter/news"]  # linked rows carry no note


def test_status_flags_unknown_kind_in_grants_file(settings):
    (settings.home / "accounts.json").write_text(
        json.dumps({"gopher/main": {"capabilities": ["dig"], "granted_at": "x"}}))
    rows = [r for r in AccountManager(settings).status()
            if r["account"] == "gopher/main"]
    assert rows and rows[0]["note"] == "unknown kind"


# -- credential resolution (env shapes; keyring absent in tests) -----------------


def test_connect_requires_grant_before_credentials(settings):
    mgr = AccountManager(settings)
    with pytest.raises(AccountError, match="no grant for email/main"):
        mgr.connect("email", "main", "read_mail")


def test_connect_reports_missing_fields_with_env_hint(settings):
    mgr = AccountManager(settings)
    mgr.grant("email", "main", ["read_mail"])
    with pytest.raises(AccountError,
                       match="missing credentials for email/main: host, port"):
        mgr.connect("email", "main", "read_mail")


def test_default_account_env_shape_resolves(settings, monkeypatch):
    monkeypatch.setenv("NEWSDESK_EMAIL_HOST", "imap.example")
    monkeypatch.setenv("NEWSDESK_EMAIL_PORT", "993")
    monkeypatch.setenv("NEWSDESK_EMAIL_USER", "me")
    monkeypatch.setenv("NEWSDESK_EMAIL_PASSWORD", "pw")
    mgr = AccountManager(settings)
    mgr.grant("email", "default", ["read_mail"])
    session = mgr.connect("email", "default", "read_mail")
    assert session.credentials == {"host": "imap.example", "port": "993",
                                   "user": "me", "password": "pw"}


def test_explicit_account_env_shape_resolves(settings, monkeypatch):
    monkeypatch.setenv("NEWSDESK_ACCOUNT__EMAIL__WORK__HOST", "imap.work")
    monkeypatch.setenv("NEWSDESK_ACCOUNT__EMAIL__WORK__PORT", "993")
    monkeypatch.setenv("NEWSDESK_ACCOUNT__EMAIL__WORK__USER", "me@work")
    monkeypatch.setenv("NEWSDESK_ACCOUNT__EMAIL__WORK__PASSWORD", "pw2")
    mgr = AccountManager(settings)
    mgr.grant("email", "work", ["read_mail"])
    session = mgr.connect("email", "work", "read_mail")
    assert session.credentials["host"] == "imap.work"


def test_youtube_kind_uses_friendly_env_prefix(settings, monkeypatch):
    """'youtube-account' reads NEWSDESK_YOUTUBE_* (not the ugly
    NEWSDESK_YOUTUBE_ACCOUNT_*) — the _ENV_KIND alias, characterized."""
    monkeypatch.setenv("NEWSDESK_YOUTUBE_API_KEY", "AIza")
    monkeypatch.setenv("NEWSDESK_YOUTUBE_CHANNEL_ID", "UC123")
    mgr = AccountManager(settings)
    mgr.grant("youtube-account", "default", ["read_subscriptions"])
    session = mgr.connect("youtube-account", "default", "read_subscriptions")
    assert session.credentials == {"api_key": "AIza", "channel_id": "UC123"}


def test_presence_never_reveals_values(settings, monkeypatch):
    monkeypatch.setenv("NEWSDESK_TWITTER_ACCESS_TOKEN", "secret-token")
    presence = __import__("newsdesk.accounts.credentials", fromlist=["presence"]).presence(
        "twitter", "default", ["access_token"])
    assert presence == {"access_token": True}  # bool, not the token


# -- session + fetcher consent chain ----------------------------------------------


def test_account_session_capability_helpers():
    s = AccountSession("email", "main", ["read_mail"], {"host": "h"})
    assert s.account_ref == "email/main"
    assert s.has("read_mail") and not s.has("send_mail")
    s.require("read_mail")
    with pytest.raises(AccountError, match="lacks capability 'send_mail'"):
        s.require("send_mail")


def test_parse_account_ref_defaults_and_reads_query():
    assert parse_account_ref("imap://host/Folder") == "default"
    assert parse_account_ref("imap://host/Folder?account=work") == "work"
    assert parse_account_ref("imap://host/Folder?account=") == "default"
    assert parse_account_ref("x://timeline/home?account=news") == "news"


class _StubFetcher(AccountFetcher):
    """Minimal provider used to exercise the base-class consent chain."""

    kind = "stub"
    account_kind = "email"
    required_capability = "read_mail"

    def _fetch(self, source, settings, session):
        from newsdesk.ingest.base import RawCapture

        self.captured_session = session
        return RawCapture(source_url=source.url, fetched_at=_NOW,
                          content_hash="h", extraction_method="stub")


def test_account_fetcher_enforces_chain_and_stamps_actor_ref(settings):
    monkey_managed = AccountManager(settings)
    monkey_managed.grant("email", "main", ["read_mail"])
    import newsdesk.accounts.manager as manager_mod
    import newsdesk.accounts.base as base_mod

    real_connect = AccountManager.connect

    def fake_connect(self, kind, ref, capability):
        session = real_connect(self, kind, ref, capability)
        session.credentials = dict(session.credentials, host="fake")
        return session

    # env creds present so resolution succeeds (ref "main" -> ACCOUNT__ shape)
    import os
    os.environ["NEWSDESK_ACCOUNT__EMAIL__MAIN__HOST"] = "h"
    os.environ["NEWSDESK_ACCOUNT__EMAIL__MAIN__PORT"] = "993"
    os.environ["NEWSDESK_ACCOUNT__EMAIL__MAIN__USER"] = "u"
    os.environ["NEWSDESK_ACCOUNT__EMAIL__MAIN__PASSWORD"] = "p"
    try:
        fetcher = _StubFetcher()
        source = type("S", (), {"url": "imap://host/INBOX?account=main"})()
        out = fetcher.fetch(source, settings)
        assert out.meta["account_ref"] == "email/main"  # actor tag stamped on the capture
        assert fetcher.captured_session.account_ref == "email/main"
    finally:
        for k in ("NEWSDESK_ACCOUNT__EMAIL__MAIN__HOST",
                  "NEWSDESK_ACCOUNT__EMAIL__MAIN__PORT",
                  "NEWSDESK_ACCOUNT__EMAIL__MAIN__USER",
                  "NEWSDESK_ACCOUNT__EMAIL__MAIN__PASSWORD"):
            os.environ.pop(k, None)


def test_account_fetcher_wraps_account_error_as_fetch_error(settings):
    from newsdesk.ingest.base import FetchError

    fetcher = _StubFetcher()
    source = type("S", (), {"url": "imap://host/INBOX?account=main"})()
    with pytest.raises(FetchError, match="account email"):
        fetcher.fetch(source, settings)  # no grant -> AccountError -> FetchError
