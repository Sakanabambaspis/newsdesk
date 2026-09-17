"""Account-linked ingestion: providers collect under explicit consent.

Email and Twitter register as pipeline fetchers (kinds ``email`` /
``twitter``); the YouTube account is a subscription-sync action. All of them
resolve credentials per-run from the keyring or environment and log under an
``account:<kind>/<ref>`` actor. See docs/DESIGN.md section 18.
"""

from . import email, twitter  # noqa: F401  -- registers account fetchers
from .base import AccountError, AccountFetcher, AccountSession, parse_account_ref  # noqa: F401
from .manager import AccountManager, PROVIDERS  # noqa: F401
