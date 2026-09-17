"""Fetcher interface and registry.

Every fetcher turns a registered Source into a RawCapture: raw bytes hashed
and snapshotted, plus parsed RawEntries. New source kinds (html, youtube,
api, forum, newsletter, manual) implement this interface and register
themselves; nothing else in the pipeline knows how bytes arrived.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Type

from ..config import Settings
from ..core.models import SOURCE_KINDS, Source


class FetchError(Exception):
    """A source could not be fetched or parsed. Message is safe to log/store."""


@dataclass
class RawEntry:
    url: str
    title: str = ""
    text: str = ""
    published_at: datetime | None = None
    author: str | None = None
    media: list[dict[str, Any]] = field(default_factory=list)
    transcript: str | None = None  # caption/transcript text when the source carries it


@dataclass
class RawCapture:
    source_url: str
    fetched_at: datetime
    content_hash: str  # hash of the raw payload bytes
    extraction_method: str  # "rss" | "html" | ...
    snapshot_path: str | None = None
    entries: list[RawEntry] = field(default_factory=list)
    status: str = "ok"  # ok | not_modified | skipped
    meta: dict[str, Any] = field(default_factory=dict)  # feed_title, etag, last_modified, ...
    content_kind: str = "article"  # Item.kind for normalized entries (article | video | post | document)


class Fetcher(ABC):
    kind: str = ""

    @abstractmethod
    def fetch(self, source: Source, settings: Settings, http: Any | None = None) -> RawCapture:
        """Fetch and parse a source. Raises FetchError on failure."""


_REGISTRY: dict[str, Type[Fetcher]] = {}


def register(cls: Type[Fetcher]) -> Type[Fetcher]:
    if cls.kind not in SOURCE_KINDS:
        raise ValueError(f"fetcher kind '{cls.kind}' is not in SOURCE_KINDS")
    _REGISTRY[cls.kind] = cls
    return cls


def get_fetcher(kind: str) -> Type[Fetcher] | None:
    return _REGISTRY.get(kind)
