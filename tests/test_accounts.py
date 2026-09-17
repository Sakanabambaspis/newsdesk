"""Account provider tests — offline: fake IMAP client, mocked API seams."""

from __future__ import annotations

import json

import pytest

import newsdesk.accounts.email as email_mod
import newsdesk.accounts.twitter as twitter_mod
import newsdesk.accounts.youtube_account as yt_mod
from newsdesk.accounts.base import AccountError, parse_account_ref
from newsdesk.accounts.manager import AccountManager
from newsdesk.core.ids import item_id_for
from newsdesk.ingest.base import FetchError, get_fetcher
from newsdesk.pipeline.runner import run_collection
from newsdesk.storage.repo import ItemRepo, LogRepo, SourceRepo

RFC822_A = (b"From: Alpha News <news@alpha.example>\r\n"
            b"Subject: Alpha weekly #12\r\n"
            b"Date: Mon, 07 Sep 2026 09:00:00 +0000\r\n"
            b"Message-ID: <alpha-12@alpha.example>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Hello from Alpha.\r\nSecond line.\r\n")

RFC822_B = (b"From: Beta Brief <hi@beta.example>\r\n"
            b"Subject: Beta digest\r\n"
            b"Date: Tue, 08 Sep 2026 10:00:00 +0000\r\n"
            b"Message-ID: <beta-8@beta.example>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Beta body.\r\n")

RFC822_HTML = (b"From: Gamma <gamma@example>\r\n"
               b"Subject: <b>Gamma</b> html-only\r\n"
               b"Date: Wed, 09 Sep 2026 11:00:00 +0000\r\n"
               b"Message-ID: <g9@gamma.example>\r\n"
               b"Content-Type: text/html; charset=utf-8\r\n\r\n"
               b"<html><body><p>Gamma html body</p></body></html>")


class FakeIMAP:
    """Just enough IMAP for the email fetcher: readonly select + UID ops."""
    state = {"uids": {101: RFC822_A, 102: RFC822_B, 103: RFC822_HTML},
             "fail_login": False, "calls": []}

    def __init__(self, host, port):
        type(self).instances = getattr(type(self), "instances", []) + [self]
        self.selected = None

    def login(self, user, password):
        self.__class__.state["calls"].append(("login", user, password))
        if self.__class__.state["fail_login"]:
            raise Exception("AUTHENTICATIONFAILED")

    def select(self, mailbox, readonly=False):
        self.selected = (mailbox, readonly)
        self.__class__.state["calls"].append(("select", mailbox, readonly))
        return "OK", [b"3"]

    def uid(self, command, *args):
        self.__class__.state["calls"].append(("uid", command, args))
        if command == "SEARCH":
            uids = sorted(self.__class__.state["uids"])
            return "OK", [b" ".join(str(u).encode() for u in uids)]
        if command == "FETCH":
            uid = int(args[0])
            assert "BODY.PEEK[]" in args[1]  # never marks messages seen
            raw = self.__class__.state["uids"].get(uid)
            if raw is None:
                return "OK", [b")"]
            return "OK", [(f"{uid} (BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        return "NO", [b"unsupported"]

    def logout(self):
        return "BYE", None


@pytest.fixture
def imap(monkeypatch):
    FakeIMAP.instances = []
    FakeIMAP.state = {"uids": {101: RFC822_A, 102: RFC822_B, 103: RFC822_HTML},
                      "fail_login": False, "calls": []}

    def fake_connect(creds):
        client = FakeIMAP(creds["host"], int(creds.get("port", 993)))
        client.login(creds["user"], creds["password"])  # real connect logs in too
        return client

    monkeypatch.setattr(email_mod, "imap_connect", fake_connect)
    return FakeIMAP


@pytest.fixture
def email_env(monkeypatch):
    monkeypatch.setenv("NEWSDESK_EMAIL_HOST", "mail.example.com")
    monkeypatch.setenv("NEWSDESK_EMAIL_PORT", "993")
    monkeypatch.setenv("NEWSDESK_EMAIL_USER", "agent@example.com")
    monkeypatch.setenv("NEWSDESK_EMAIL_PASSWORD", "hunter2")
    monkeypatch.delenv("NEWSDESK_ACCOUNT__EMAIL__WORK__HOST", raising=False)


@pytest.fixture
def email_grant(settings):
    AccountManager(settings).grant("email", "default", ["read_mail"])


def _email_source(session, url="imap://mail.example.com/News?account=default"):
    source, _ = SourceRepo(session).add(url, kind="email", title="Mail News")
    return source


def test_account_fetchers_registered():
    assert get_fetcher("email").kind == "email"
    assert get_fetcher("twitter").kind == "twitter"


def test_twitter_items_normalize_to_post_kind(session, settings, twitter_env,
                                              monkeypatch):
    """Content kind belongs to the capture (audit D3 fixed): twitter items
    are posts, decided by the fetcher, not the pipeline."""
    from newsdesk.pipeline.normalize import normalize_capture

    def fake_api(path, params, token):
        if path == "/users/me":
            return {"data": {"id": "777", "username": "me"}}
        return TIMELINE

    monkeypatch.setattr(twitter_mod, "api_get", fake_api)
    AccountManager(settings).grant("twitter", "default", ["read_timeline"])
    source, _ = SourceRepo(session).add("x://timeline/home", kind="twitter")
    capture = get_fetcher("twitter")().fetch(source, settings)
    items = normalize_capture(capture, source)
    assert items and all(i["source"]["kind"] == "post" for i in items)


def test_email_test_connection_probe(session, settings, imap, email_env,
                                     email_grant):
    """`newsdesk accounts test` drives the provider's public probe, not its
    privates (audit D5 fixed)."""
    from newsdesk.accounts.email import test_connection

    linked = AccountManager(settings).connect("email", "default", "read_mail")
    assert test_connection(linked) == "connection ok"


def test_parse_account_ref():
    assert parse_account_ref("imap://h/INBOX?account=work") == "work"
    assert parse_account_ref("x://timeline/home") == "default"
    assert parse_account_ref("imap://h/INBOX?account=") == "default"


def test_grant_requires_known_kind_and_caps(settings):
    manager = AccountManager(settings)
    with pytest.raises(AccountError):
        manager.grant("carrier-pigeon", "default", ["read_mail"])
    with pytest.raises(AccountError):
        manager.grant("email", "default", ["read_everything"])
    entry = manager.grant("email", "default", ["read_mail"])
    assert entry["capabilities"] == ["read_mail"]
    # grants merge, not overwrite
    manager.grant("email", "default", ["read_mail"])
    assert manager.grants()["email/default"]["capabilities"] == ["read_mail"]


def test_connect_blocks_without_grant_or_credentials(settings, email_env):
    manager = AccountManager(settings)
    with pytest.raises(AccountError, match="no grant"):
        manager.connect("email", "default", "read_mail")
    manager.grant("email", "default", ["read_mail"])
    import os
    monkey = pytest.MonkeyPatch()
    monkey.delenv("NEWSDESK_EMAIL_PASSWORD")
    monkey.delenv("NEWSDESK_ACCOUNT__EMAIL__DEFAULT__PASSWORD", raising=False)
    try:
        with pytest.raises(AccountError, match="missing credentials.*password"):
            manager.connect("email", "default", "read_mail")
    finally:
        monkey.undo()


def test_email_fetch_collects_messages(session, settings, imap, email_env, email_grant):
    source = _email_source(session)
    capture = get_fetcher("email")().fetch(source, settings)

    assert capture.status == "ok"
    assert capture.extraction_method == "email"
    assert capture.meta["account_ref"] == "email/default"
    assert capture.meta["etag"] == "imap-uid:103"
    assert len(capture.entries) == 3
    first = capture.entries[0]
    assert first.title == "Alpha weekly #12"
    assert first.author.startswith("Alpha News")
    assert "Hello from Alpha." in first.text
    assert first.url.startswith("mid:")
    html_entry = capture.entries[2]
    assert html_entry.text == "Gamma html body"  # html stripped as fallback
    # readonly mailbox access only
    selects = [c for c in imap.state["calls"] if c[0] == "select"]
    assert selects and all(sel[2] is True for sel in selects)
    assert capture.snapshot_path.endswith(".email.json")


def test_email_incremental_uses_uid_highwater(session, settings, imap, email_env,
                                              email_grant):
    source = _email_source(session)
    capture = get_fetcher("email")().fetch(source, settings)
    assert capture.meta["etag"] == "imap-uid:103"

    # simulate the runner persisting state, then a run with one new message
    SourceRepo(session).record_fetch(source, status="ok", etag=capture.meta["etag"])
    imap.state["uids"][104] = RFC822_B.replace(b"beta-8@beta.example",
                                               b"beta-9@beta.example")
    capture2 = get_fetcher("email")().fetch(source, settings)
    assert capture2.meta["etag"] == "imap-uid:104"
    assert len(capture2.entries) == 1


def test_email_items_upsert_and_dedupe(session, settings, imap, email_env, email_grant):
    source = _email_source(session)
    capture = get_fetcher("email")().fetch(source, settings)
    from newsdesk.pipeline.normalize import normalize_capture
    items = normalize_capture(capture, source)
    repo = ItemRepo(session)
    for item in items:
        outcome, _ = repo.upsert(item, source_id=source.id)
        assert outcome == "created"
        assert item_id_for(item["source"]["url"]) == item["id"]  # stable ids
    for item in items:
        outcome, _ = repo.upsert(item, source_id=source.id)
        assert outcome == "unchanged"


def test_runner_actor_tags_account_fetches(session, settings, imap, email_env,
                                           email_grant):
    source = _email_source(session)
    run_collection(session, settings, [source.id])
    entries = [e for e in LogRepo(session).recent(limit=20)
               if e.action == "source_collected"]
    assert entries and entries[0].actor == "account:email/default"
    assert entries[0].detail.get("entries") == 3


def test_email_login_failure_is_fetch_error(session, settings, imap, email_env,
                                            email_grant):
    imap.state["fail_login"] = True
    source = _email_source(session)
    with pytest.raises(FetchError, match="IMAP connect/login failed"):
        get_fetcher("email")().fetch(source, settings)


def test_no_grant_fails_collect_cleanly(session, settings, imap, email_env):
    source = _email_source(session)  # env creds set, but no grant
    job = run_collection(session, settings, [source.id])
    assert job.status == "partial"
    assert "account email:" in job.stats["sources"][str(source.id)]["error"]


# -- twitter ----------------------------------------------------------------

TIMELINE = {
    "data": [
        {"id": "9002", "text": "Second tweet", "author_id": "u1",
         "created_at": "2026-09-08T10:00:00.000Z"},
        {"id": "9001", "text": "First tweet with link https://example.com",
         "author_id": "u2", "created_at": "2026-09-08T09:00:00.000Z",
         "attachments": {"media_keys": ["7_1"]}},
    ],
    "includes": {
        "users": [{"id": "u1", "username": "alice"},
                  {"id": "u2", "username": "bob"}],
        "media": [{"media_key": "7_1", "type": "photo",
                   "url": "https://pbs.example/7_1.jpg"}],
    },
}


@pytest.fixture
def twitter_env(monkeypatch):
    monkeypatch.setenv("NEWSDESK_TWITTER_ACCESS_TOKEN", "tok-1")


def test_twitter_fetch(session, settings, twitter_env, monkeypatch):
    calls = []

    def fake_api(path, params, token):
        calls.append((path, params, token))
        if path == "/users/me":
            return {"data": {"id": "777", "username": "me"}}
        assert "reverse_chronological" in path
        assert params["tweet.fields"].startswith("created_at")
        assert token == "tok-1"
        return TIMELINE

    monkeypatch.setattr(twitter_mod, "api_get", fake_api)
    AccountManager(settings).grant("twitter", "default", ["read_timeline"])
    source, _ = SourceRepo(session).add("x://timeline/home", kind="twitter")

    capture = get_fetcher("twitter")().fetch(source, settings)
    assert capture.meta["account_ref"] == "twitter/default"
    assert capture.meta["etag"] == "x-since:9002"
    assert len(capture.entries) == 2
    oldest, newest = capture.entries
    assert oldest.url == "https://x.com/bob/status/9001"
    assert oldest.author == "@bob"
    assert oldest.media == [{"url": "https://pbs.example/7_1.jpg", "type": "photo"}]
    assert newest.url == "https://x.com/alice/status/9002"

    # since_id flows from persisted state
    SourceRepo(session).record_fetch(source, status="ok", etag=capture.meta["etag"])
    get_fetcher("twitter")().fetch(source, settings)
    assert any(p.get("since_id") == "9002" for _, p, _ in calls)


def test_twitter_api_error_is_fetch_error(session, settings, twitter_env, monkeypatch):
    def fake_api(path, params, token):
        raise FetchError("X API HTTP 401 for /users/me: unauthorized")

    monkeypatch.setattr(twitter_mod, "api_get", fake_api)
    AccountManager(settings).grant("twitter", "default", ["read_timeline"])
    source, _ = SourceRepo(session).add("x://timeline/home", kind="twitter")
    with pytest.raises(FetchError, match="401"):
        get_fetcher("twitter")().fetch(source, settings)


# -- youtube account sync ------------------------------------------------------

SUBSCRIPTIONS = {
    "items": [
        {"snippet": {"title": "Channel A",
                     "resourceId": {"channelId": "UCa"}}},
        {"snippet": {"title": "Channel B",
                     "resourceId": {"channelId": "UCb"}}},
    ],
}


def test_youtube_subscription_sync(session, settings, monkeypatch):
    monkeypatch.setenv("NEWSDESK_YOUTUBE_API_KEY", "yt-key")
    monkeypatch.setenv("NEWSDESK_YOUTUBE_CHANNEL_ID", "UCme")
    monkeypatch.setattr(yt_mod, "api_get",
                        lambda path, params, key: SUBSCRIPTIONS)

    AccountManager(settings).grant("youtube-account", "default",
                                   ["read_subscriptions"])
    report = yt_mod.sync_subscriptions(session, settings)

    assert report["registered"] == 2
    sources = {s.title: s for s in SourceRepo(session).list()}
    assert set(sources) == {"Channel A", "Channel B"}
    assert sources["Channel A"].kind == "youtube"

    # idempotent: second sync registers nothing new
    report2 = yt_mod.sync_subscriptions(session, settings)
    assert report2["registered"] == 0 and report2["already_present"] == 2

    log = [e for e in LogRepo(session).recent(limit=20) if e.action == "source_added"]
    assert all(e.actor == "account:youtube-account/default" for e in log)
    assert log and log[0].detail.get("via") == "subscriptions-sync"
