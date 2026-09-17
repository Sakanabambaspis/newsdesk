"""Transcript acquisition: captions first, audio transcription as fallback.

Caption tracks are fetched during ingestion (no download, no model). Audio
transcription downloads the audio stream and POSTs it to an OpenAI-compatible
``/audio/transcriptions`` endpoint — enabled explicitly via
``NEWSDESK_TRANSCRIBE_AUDIO=1`` because it costs bandwidth and tokens.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..llm.base import LLMError, LLMNotConfigured

_VTT_TIMESTAMP = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3} --> ")
_VTT_TAG = re.compile(r"<[^>]+>")


def parse_vtt(payload: str) -> str:
    """WebVTT caption text -> plain transcript, collapsing rolling repeats."""
    lines: list[str] = []
    for raw_line in payload.splitlines():
        line = _VTT_TAG.sub("", raw_line).strip()
        if not line or line in ("WEBVTT", "NOTE") or line.startswith(("Kind:", "Language:")):
            continue
        if _VTT_TIMESTAMP.match(line) or line.isdigit():  # cue header / cue id
            continue
        if not lines or lines[-1] != line:  # auto-captions repeat the rolling line
            lines.append(line)
    return "\n".join(lines)


def parse_json3(payload: str) -> str:
    """YouTube json3 caption format -> plain transcript."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    parts: list[str] = []
    for event in data.get("events", []):
        segs = event.get("segs") or []
        chunk = "".join(s.get("utf8", "") for s in segs).strip()
        if chunk and (not parts or parts[-1] != chunk):
            parts.append(chunk)
    return "\n".join(parts)


def caption_text(track_payload: str, fmt: str) -> str:
    if fmt == "json3":
        return parse_json3(track_payload)
    return parse_vtt(track_payload)


def pick_caption_track(tracks: dict[str, list[dict[str, Any]]] | None,
                       prefer_langs: tuple[str, ...] = ("en", "en-US", "en-GB")) \
        -> tuple[str, str, str] | None:
    """Choose (language, url, format) for the best caption track, or None.

    ``tracks`` is the yt-dlp shape: {language: [{url, ext, ...}, ...]}.
    Prefers manual subtitles over auto captions by call order — callers pass
    ``info["subtitles"]`` first and fall back to ``info["automatic_captions"]``.
    """
    if not tracks:
        return None
    candidates = list(prefer_langs) + sorted(
        lang for lang in tracks if lang not in prefer_langs
    )
    for lang in candidates:
        if lang not in tracks:
            continue
        for want in ("json3", "vtt", "srt"):  # formats we can parse
            for track in tracks[lang]:
                if track.get("ext") == want and track.get("url"):
                    return lang, track["url"], want
    return None


class AudioTranscriber:
    """OpenAI-compatible audio transcription (whisper-style endpoints).

    Works against any server speaking ``POST {base_url}/audio/transcriptions``
    — hosted APIs or local whisper servers (faster-whisper-server, LocalAI,
    whisper.cpp server). Raises LLMError so callers can log-and-continue.
    """

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        if not (settings.llm_base_url and settings.llm_api_key):
            raise LLMNotConfigured(
                "Audio transcription needs NEWSDESK_LLM_BASE_URL and NEWSDESK_LLM_API_KEY."
            )
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_audio_model
        self.timeout = max(settings.request_timeout, 120.0)
        self.transport = transport

    def transcribe_file(self, path: Path) -> str:
        with open(path, "rb") as audio, httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            response = client.post(
                "/audio/transcriptions",
                files={"file": (path.name, audio)},
                data={"model": self.model, "response_format": "json"},
            )
        if response.status_code >= 400:
            raise LLMError(f"transcription HTTP {response.status_code}: {response.text[:200]}")
        return response.json().get("text", "")


def download_audio(url: str, dest_dir: Path, settings: Settings,
                   ffmpeg_location: str | None = None) -> Path | None:
    """Download the best audio stream for a video URL via yt-dlp.

    Audio-only keeps bandwidth low; when ffmpeg is available the stream is
    also transcoded to mp3, which every transcription endpoint accepts.
    """
    try:
        import yt_dlp
    except ImportError:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(dest_dir / "audio.%(ext)s"),
        "quiet": True,
        "noprogress": True,
    }
    if ffmpeg_location:
        opts["ffmpeg_location"] = ffmpeg_location
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception:
        return None
    for candidate in sorted(dest_dir.glob("audio.*")):
        return candidate
    return None
