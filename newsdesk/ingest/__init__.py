"""Ingestion package. Importing fetcher modules here registers them in the
fetcher registry (ingest.base._REGISTRY) used by the pipeline runner."""

from . import rss  # noqa: F401  -- registers the RSS/Atom fetcher
