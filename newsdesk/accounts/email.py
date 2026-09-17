"""Email provider: read-only IMAP collection of one mailbox folder.

- Read-only SELECT; ``BODY.PEEK[]`` fetches never mark messages as seen,
  and nothing is ever written back to the server.
- Incremental via UID high-water mark stored in the source's etag token
  (``imap-uid:<n>``), so each run reads only new messages.
- Item URLs are RFC 2392 ``mid:<message-id>`` URIs — stable, dedupe-friendly
  identities that don't expose the mailbox.
- Source URL shape: ``imap://imap.example.com/Newsletter?account=main``
  (host/port/credentials come from the account, not the URL).
"""

from __future__ import annotations

import email as email_lib
import email.policy
import email.utils
import imaplib
import json
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlsplit

from ..config import Settings
from ..core.ids import sha256_hex
from ..ingest.base import FetchError, RawCapture, RawEntry, register
from ..ingest.snapshots import save_snapshot
from ..ingest.textutil import strip_html
from .base import AccountFetcher, AccountSession

BODY_LIMIT = 200_000  # chars kept per message
SNAPSHOT_LIMIT = 500_000  # chars of raw RFC822 kept per message in the snapshot


def imap_connect(creds: dict[str, str]):
    """Seam for tests and the CLI probe: a connected, logged-in IMAP client.

    Port 993 (default) uses IMAPS/TLS; any other port connects in plaintext —
    for local/test servers only, never for real mailboxes.
    """
    port = int(creds.get("port") or 993)
    client = (imaplib.IMAP4_SSL(creds["host"], port) if port == 993
              else imaplib.IMAP4(creds["host"], port))
    client.login(creds["user"], creds["password"])
    return client


def test_connection(session: AccountSession) -> str:
    """Read-only connectivity probe behind `newsdesk accounts test`."""
    imap_connect(session.credentials).logout()
    return "connection ok"


def _mailbox_from_url(source_url: str) -> str:
    path = unquote(urlsplit(source_url).path).strip("/")
    return path or "INBOX"


def _parse_message(raw: bytes) -> RawEntry | None:
    msg = email_lib.message_from_bytes(raw, policy=email.policy.default)
    message_id = (msg.get("Message-ID") or "").strip().strip("<>")
    if not message_id:  # nothing stable to anchor provenance to
        return None
    subject = str(msg.get("Subject") or "").strip()
    author = str(msg.get("From") or "").strip() or None
    published = None
    try:
        raw_date = msg.get("Date")
        if raw_date:
            published = email.utils.parsedate_to_datetime(str(raw_date))
    except (TypeError, ValueError):
        published = None

    body = ""
    html_body = ""
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and not body:
            try:
                body = part.get_content()
            except (LookupError, UnicodeDecodeError):
                pass
        elif ctype == "text/html" and not html_body:
            try:
                html_body = part.get_content()
            except (LookupError, UnicodeDecodeError):
                pass
    text = (body or strip_html(html_body)).strip()[:BODY_LIMIT]

    return RawEntry(
        url=f"mid:{quote(message_id, safe='@.%+-_')}",
        title=subject,
        text=text,
        published_at=published,
        author=author,
        media=[],
    )


@register
class EmailFetcher(AccountFetcher):
    kind = "email"
    account_kind = "email"
    required_capability = "read_mail"

    def _fetch(self, source, settings: Settings, session: AccountSession) -> RawCapture:
        creds = session.credentials
        mailbox = _mailbox_from_url(source.url)
        fetched_at = datetime.now(timezone.utc)

        last_uid = 0
        if (source.etag or "").startswith("imap-uid:"):
            try:
                last_uid = int(source.etag.split(":", 1)[1])
            except ValueError:
                last_uid = 0

        try:
            client = imap_connect(creds)
        except Exception as exc:
            raise FetchError(f"IMAP connect/login failed for {creds.get('host')}: "
                             f"{type(exc).__name__}") from exc
        window: list[int] = []
        try:
            status, _ = client.select(mailbox, readonly=True)
            if status != "OK":
                raise FetchError(f"cannot open mailbox '{mailbox}' (read-only)")
            status, data = client.uid("SEARCH", None, "ALL")
            if status != "OK":
                raise FetchError("UID SEARCH failed")
            uids = [int(u) for u in (data[0] or b"").split()]
            new_uids = [u for u in uids if u > last_uid]
            # cap to the newest N but keep ascending order for the log
            window = new_uids[-settings.max_items_per_feed:]

            entries: list[RawEntry] = []
            raw_messages: list[dict] = []
            for uid in window:
                status, data = client.uid(
                    "FETCH", str(uid), "(BODY.PEEK[] INTERNALDATE)")
                if status != "OK":
                    continue
                raw = next((part[1] for part in data
                            if isinstance(part, tuple)), None)
                if not raw:
                    continue
                entry = _parse_message(raw)
                if entry is None:
                    continue
                entries.append(entry)
                raw_messages.append({
                    "uid": uid,
                    "message_id": entry.url[4:],
                    "subject": entry.title,
                    "from": entry.author,
                    "rfc822": raw.decode("utf-8", errors="replace")[:SNAPSHOT_LIMIT],
                })
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError(f"IMAP error: {type(exc).__name__}") from exc
        finally:
            try:
                client.logout()
            except Exception:
                pass

        payload = json.dumps({
            "account": session.account_ref, "mailbox": mailbox,
            "uids_seen": window, "messages": raw_messages,
        }, ensure_ascii=False, indent=1).encode("utf-8")
        snapshot_path = save_snapshot(settings, source_id=source.id,
                                      payload=payload, ext="email.json",
                                      when=fetched_at)

        return RawCapture(
            source_url=source.url,
            fetched_at=fetched_at,
            content_hash=sha256_hex(payload),
            extraction_method=self.kind,
            snapshot_path=snapshot_path,
            entries=entries,
            status="ok",
            content_kind="document",
            meta={
                "etag": f"imap-uid:{max(window) if window else last_uid}",
                "mailbox": mailbox,
                "new_messages": len(entries),
            },
        )
