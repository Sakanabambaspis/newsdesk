"""Summarization wiring (M3 minimal) + the video digest pass.

``summarize_item`` turns one canonical item into an evidence-linked summary
via the LLM adapter, or an honestly-labeled extractive summary when no LLM
is configured — the loop never crashes for want of a key.

``digest_item`` is the "watch a video" pass: ensure a transcript (captions
already collected -> optional audio transcription), extract and describe
keyframes (vision), then summarize text + transcript + visuals together.
Every step degrades independently and records what it did.
"""

from __future__ import annotations

import re
from typing import Any

from ..config import Settings
from ..llm.base import BaseLLMAdapter, LLMError, get_adapter
from ..media.vision import ffmpeg_exe
from ..storage.repo import ItemRepo, LogRepo

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _top_sentences(text: str, limit: int = 3) -> list[str]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if len(s.strip()) > 30]
    return sentences[:limit]


def extractive_summary(item: dict[str, Any]) -> dict[str, Any]:
    """No-LLM fallback: leading sentences, clearly labeled as extractive."""
    content = item.get("content", {})
    # Transcript first: video descriptions are mostly links/sponsor notes.
    parts = [p for p in (content.get("transcript") or "", content.get("text") or "") if p]
    sentences = _top_sentences("\n".join(parts))
    return {
        "headline": content.get("title") or "",
        "what_happened": [f"{s} (extractive)" for s in sentences],
        "when": (item.get("timestamps") or {}).get("published_at"),
        "who_reported": [{
            "publisher": (item.get("source") or {}).get("publisher"),
            "item_id": item.get("id"),
        }],
        "directly_supported": [],
        "uncertain": ["No LLM configured; this is an extractive (first-sentences) "
                      "summary, not a grounded analysis."],
        "changed_vs_earlier": None,
        "method": "extractive",
    }


def summarize_item(session, settings: Settings, item_id: str,
                   *, force: bool = False,
                   adapter: BaseLLMAdapter | None = None) -> dict[str, Any]:
    """Summarize one item and store the result in analysis.summary."""
    repo = ItemRepo(session)
    row = repo.get(item_id)
    if not row:
        return {"error": "not_found", "item_id": item_id}
    canonical = row.to_canonical()
    existing = (row.analysis or {}).get("summary")
    if existing and not force:
        return existing

    adapter = adapter or get_adapter(settings)
    try:
        summary = adapter.summarize_items([canonical])
    except LLMError:
        summary = {"error": "llm_error"}
    if summary.get("error") in ("llm_not_configured", "llm_error"):
        summary = extractive_summary(canonical)
    else:
        summary.setdefault("method", f"llm:{adapter.name}")

    repo.patch_analysis(item_id, {"summary": summary})
    LogRepo(session).append("item_summarized", {
        "item_id": item_id,
        "method": summary.get("method", summary.get("error")),
    })
    return summary


def digest_item(session, settings: Settings, item_id: str,
                *, force: bool = False, with_vision: bool = True,
                transcriber: Any = None, vision: Any = None) -> dict[str, Any]:
    """Watch one video item: transcript -> keyframes/vision -> summary.

    ``transcriber``/``vision`` are injection seams for tests; production
    adapters are built from settings. Returns a report of what was done.
    """
    repo = ItemRepo(session)
    log = LogRepo(session)
    row = repo.get(item_id)
    if not row:
        return {"error": "not_found", "item_id": item_id}
    if row.kind != "video":
        # Text items can still be digested; they just skip the media passes.
        summary = summarize_item(session, settings, item_id, force=force)
        return {"item_id": item_id, "kind": row.kind, "summary": summary,
                "transcript": "n/a", "vision": "n/a"}

    report: dict[str, Any] = {"item_id": item_id, "kind": "video"}

    # 1. Transcript: already collected from captions, else optionally transcribe.
    transcript_status = "captions"
    if not row.transcript:
        transcript_status = "none"
        if settings.transcribe_audio:
            from ..media.transcripts import AudioTranscriber, download_audio
            try:
                transcriber = transcriber or AudioTranscriber(settings)
                audio = download_audio(row.url, settings.media_dir / item_id / "audio",
                                       settings, ffmpeg_location=ffmpeg_exe())
                if audio:
                    text = transcriber.transcribe_file(audio)
                    if text:
                        repo.set_transcript(item_id, text)
                        repo.patch_analysis(item_id, {
                            "transcript_source": {"method": "audio-transcription",
                                                  "model": settings.llm_audio_model},
                        })
                        transcript_status = "audio-transcription"
            except LLMError as exc:
                transcript_status = f"transcription-unavailable: {exc}"
    report["transcript"] = transcript_status

    # 2. Vision: download video, extract keyframes, describe them.
    vision_status = "skipped"
    if with_vision:
        if not (settings.llm_base_url and settings.llm_api_key):
            vision_status = "skipped:no-llm-key"
        elif not (ffmpeg := ffmpeg_exe()):
            vision_status = "skipped:no-ffmpeg"
        else:
            from ..media import vision as vision_mod
            try:
                describer = vision or vision_mod.VisionDescriber(settings)
                media_dir = settings.media_dir / item_id
                video = vision_mod.download_video(row.url, media_dir, settings,
                                                  ffmpeg_location=ffmpeg)
                if not video:
                    vision_status = "download-failed"
                else:
                    frames = vision_mod.extract_keyframes(
                        video, media_dir / "frames", settings.keyframe_interval)
                    if not frames:
                        vision_status = "no-frames-extracted"
                    else:
                        description = describer.describe_frames(
                            frames, context=row.title or "")
                        repo.patch_analysis(item_id, {"visual": {
                            "description": description,
                            "frames": len(frames),
                            "frame_interval_s": settings.keyframe_interval,
                            "model": settings.vision_model or settings.llm_model,
                        }})
                        vision_status = f"described:{len(frames)}-frames"
                        log.append("item_visual_described", {
                            "item_id": item_id, "frames": len(frames)})
            except LLMError as exc:
                vision_status = f"vision-error: {exc}"
    report["vision"] = vision_status

    # 3. Summarize over text + transcript + visuals.
    report["summary"] = summarize_item(session, settings, item_id, force=force)
    log.append("item_digested", {"item_id": item_id,
                                 "transcript": transcript_status,
                                 "vision": vision_status})
    return report
