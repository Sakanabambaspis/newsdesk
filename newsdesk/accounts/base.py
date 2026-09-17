"""Account contracts: consent, credential handles, and provider seams.

Design rules (deliberately enforced by shape, not just docs):

- Credentials never touch the database or the immutable log. Providers
  receive a resolved credential dict fetched per-run from the OS keyring or
  environment (``newsdesk.accounts.credentials``); rows store only the
  opaque ``account_ref`` label.
- Capability-scoped consent: an account link is a grant (``accounts.json``
  under NEWSDESK_HOME) listing exactly what the agent may do, e.g.
  ``email/main: ["read_mail"]``. Providers refuse anything broader.
- Authenticated fetches produce the same ``RawCapture`` as public-web
  fetchers, so normalization, dedupe, provenance, and the log apply
  unchanged — and the runner logs them under an ``account:<kind>/<ref>``
  actor so "what did the agent do under my identity" stays answerable.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from ..config import Settings
from ..ingest.base import Fetcher


class AccountError(Exception):
    """Missing grant/credentials or refused action. Message is safe to log."""


class AccountSession:
    """A consent + credential view of one linked account for one run."""

    def __init__(self, kind: str, ref: str, capabilities: list[str],
                 credentials: dict[str, str]):
        self.kind = kind  # "email" | "twitter" | "youtube-account" | ...
        self.ref = ref  # opaque label, e.g. "main"; stored in logs as kind/ref
        self.capabilities = capabilities  # granted capability ids
        self.credentials = credentials  # resolved this run; never persisted

    @property
    def account_ref(self) -> str:
        return f"{self.kind}/{self.ref}"

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def require(self, capability: str) -> None:
        if not self.has(capability):
            raise AccountError(
                f"account {self.account_ref} lacks capability '{capability}'; "
                f"grant it with: newsdesk accounts grant {self.kind} {self.ref} "
                f"--cap {capability}"
            )


def parse_account_ref(source_url: str, default: str = "default") -> str:
    """Account label travels in the source URL: scheme://host/path?account=ref."""
    query = parse_qs(urlsplit(source_url).query)
    values = query.get("account")
    return (values[0].strip() if values and values[0].strip() else default)


class AccountFetcher(Fetcher):
    """Base for account kinds that collect items like any other source.

    Subclasses set ``account_kind`` and ``required_capability`` and implement
    ``_fetch(source, settings, session)``. ``fetch`` enforces the consent
    chain (grant -> credentials -> capability) and stamps the capture with
    the account ref so the runner can actor-tag the log entry.
    """

    account_kind: str = ""
    required_capability: str = ""

    def fetch(self, source, settings: Settings, http=None):  # type: ignore[override]
        from .manager import AccountManager

        manager = AccountManager(settings)
        try:
            session = manager.connect(self.account_kind,
                                      parse_account_ref(source.url),
                                      self.required_capability)
            capture = self._fetch(source, settings, session)
        except AccountError as exc:
            from ..ingest.base import FetchError

            raise FetchError(f"account {self.account_kind}: {exc}") from exc
        capture.meta["account_ref"] = session.account_ref
        return capture

    def _fetch(self, source, settings: Settings, session: AccountSession):
        raise NotImplementedError
