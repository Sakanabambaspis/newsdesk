"""Video-platform fetcher backed by yt-dlp.

One implementation serves two registered kinds:

- ``youtube`` — channel/handle URLs (e.g. https://www.youtube.com/@TwoMinutePapers)
- ``video``   — any other yt-dlp-supported page (Bilibili spaces, etc.)

Per video we keep metadata plus a caption-derived transcript when the
platform exposes one (manual subtitles preferred over auto-captions).
Extraction goes through yt-dlp's supported access paths, so the HTTP
robots/ETag machinery of the RSS fetcher does not apply; the raw payload
snapshot is the JSON record of what we took.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import Settings
from ..core.ids import sha256_hex
from ..core.models import Source
from ..media.transcripts import caption_text, pick_caption_track
from .base import FetchError, Fetcher, RawCapture, RawEntry, register
from .snapshots import save_snapshot
from .textutil import strip_html

# Full metadata per video costs one platform request; bound the backlog.
VIDEO_DETAIL_CAP = 25
SNAPSHOT_TRANSCRIPT_LIMIT = 200_000  # chars per transcript kept in the snapshot file


def _extract_info(url: str, opts: dict[str, Any]) -> dict[str, Any]:
    """Seam for tests: real implementation delegates to yt-dlp."""
    import yt_dlp
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _fetch_caption(url: str, user_agent: str) -> str:
    """Seam for tests: download a caption track payload.

    Caption CDNs sit outside the crawl contract, but we still identify
    ourselves honestly instead of posing as a browser (DESIGN.md 5.2/11).
    """
    response = httpx.get(url, timeout=30.0, follow_redirects=True,
                         headers={"User-Agent": user_agent})
    response.raise_for_status()
    return response.text


def _fetch_url(source_url: str) -> str:
    """Normalize a source URL to the form yt-dlp's extractors match."""
    # Bilibili upload-tab links: keep the plain space URL the extractor knows.
    marker = "space.bilibili.com/"
    if marker in source_url:
        tail = source_url.split(marker, 1)[1]
        uid = tail.split("/", 1)[0]
        if uid.isdigit():
            return f"https://{marker}{uid}"
    # A bare YouTube channel/handle root lists *tabs* (Videos, Shorts), not
    # videos; pin the uploads listing directly.
    if source_url.removeprefix("https://").removeprefix("http://").startswith(
            ("www.youtube.com/@", "www.youtube.com/channel/",
             "www.youtube.com/user/", "youtube.com/@", "youtube.com/channel/",
             "youtube.com/user/")) and not source_url.rstrip("/").endswith(
            ("/videos", "/shorts", "/streams", "/featured", "/playlists")):
        return source_url.rstrip("/") + "/videos"
    return source_url


def _parse_upload_date(info: dict[str, Any]) -> datetime | None:
    ts = info.get("timestamp")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    raw = info.get("upload_date")  # "YYYYMMDD"
    if isinstance(raw, str) and len(raw) == 8 and raw.isdigit():
        return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
    return None


def _video_transcript(info: dict[str, Any], settings: Settings) -> tuple[str | None, str | None]:
    """(transcript_text, caption_language) from manual subs or auto captions."""
    for tracks in (info.get("subtitles"), info.get("automatic_captions")):
        track = pick_caption_track(tracks)
        if not track:
            continue
        lang, url, fmt = track
        try:
            payload = _fetch_caption(url, settings.user_agent)
        except Exception:
            continue
        text = caption_text(payload, fmt)
        if text:
            return text, lang
    return None, None


def _fetch_via_ytdlp(fetcher: Fetcher, source: Source, settings: Settings) -> RawCapture:
    try:
        import yt_dlp  # noqa: F401  (availability check; _extract_info does the work)
    except ImportError:
        raise FetchError(
            "yt-dlp is not installed; run: pip install 'newsdesk[media]'"
        )

    fetched_at = datetime.now(timezone.utc)
    target = _fetch_url(source.url)
    listing_opts: dict[str, Any] = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "skip_download": True,
        "noplaylist": False,
        "playlistend": min(settings.max_items_per_feed, VIDEO_DETAIL_CAP),
    }
    try:
        listing = _extract_info(target, listing_opts)
    except Exception as exc:
        raise FetchError(f"yt-dlp listing failed for {target}: {exc}") from exc

    entries = [e for e in (listing.get("entries") or []) if e]
    if not entries and listing.get("id"):  # a single video was passed, not a playlist
        entries = [listing]

    raw_entries: list[RawEntry] = []
    snapshot_videos: list[dict[str, Any]] = []
    caption_langs: set[str] = set()
    detail_opts: dict[str, Any] = {"quiet": True, "skip_download": True}

    for entry in entries[:VIDEO_DETAIL_CAP]:
        if entry.get("ie_key") == "YoutubeTab":
            continue  # a nested channel tab, not a video
        video_url = entry.get("url") or entry.get("webpage_url")
        if not video_url:
            continue
        try:
            info = _extract_info(video_url, detail_opts) or {}
        except Exception:
            info = dict(entry)  # degrade: listing-level metadata only
        webpage_url = info.get("webpage_url") or video_url
        transcript, caption_lang = _video_transcript(info, settings)
        if caption_lang:
            caption_langs.add(caption_lang)
        description = strip_html(info.get("description") or "")
        raw_entries.append(RawEntry(
            url=webpage_url,
            title=strip_html(info.get("title") or entry.get("title") or ""),
            text=description,
            published_at=_parse_upload_date(info),
            author=info.get("uploader") or info.get("channel"),
            media=[{
                "type": "video",
                "duration": info.get("duration"),
                "view_count": info.get("view_count"),
                "thumbnail": info.get("thumbnail"),
                "video_id": info.get("id"),
            }],
            transcript=transcript,
        ))
        snapshot_videos.append({
            "id": info.get("id"),
            "title": info.get("title"),
            "webpage_url": webpage_url,
            "uploader": info.get("uploader"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "upload_date": info.get("upload_date"),
            "caption_lang": caption_lang,
            "transcript": transcript[:SNAPSHOT_TRANSCRIPT_LIMIT] if transcript else None,
        })

    payload = json.dumps({
        "source_url": source.url,
        "resolved_url": target,
        "extractor": listing.get("extractor_key"),
        "channel": listing.get("channel") or listing.get("title"),
        "videos": snapshot_videos,
    }, ensure_ascii=False, indent=1).encode("utf-8")
    payload_hash = sha256_hex(payload)

    return RawCapture(
        source_url=source.url,
        fetched_at=fetched_at,
        content_hash=payload_hash,
        extraction_method=fetcher.kind,
        snapshot_path=save_snapshot(settings, source_id=source.id,
                                    payload=payload, ext="json",
                                    when=fetched_at),
        entries=raw_entries,
        status="ok",
        content_kind="video",
        meta={
            "feed_title": listing.get("channel") or listing.get("title"),
            "video_count": len(raw_entries),
            "with_transcript": sum(1 for e in raw_entries if e.transcript),
            "caption_langs": sorted(caption_langs),
        },
    )


@register
class YoutubeFetcher(Fetcher):
    kind = "youtube"

    def fetch(self, source: Source, settings: Settings, http: Any | None = None) -> RawCapture:
        return _fetch_via_ytdlp(self, source, settings)


@register
class VideoFetcher(Fetcher):
    kind = "video"

    def fetch(self, source: Source, settings: Settings, http: Any | None = None) -> RawCapture:
        return _fetch_via_ytdlp(self, source, settings)
