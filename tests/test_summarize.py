"""Summarize + digest pipeline tests (offline: fake adapters, no downloads)."""

from __future__ import annotations

import json

import httpx
import pytest

from newsdesk.llm.base import BaseLLMAdapter
from newsdesk.pipeline.runner import run_collection
from newsdesk.pipeline.summarize import digest_item, extractive_summary, summarize_item
from newsdesk.storage.repo import ItemRepo, LogRepo, SourceRepo
from tests.conftest import make_canonical_item


class FakeAdapter(BaseLLMAdapter):
    name = "fake"

    def __init__(self):
        self.seen_blocks: list[str] = []

    def complete(self, system, user, *, max_tokens=1200, temperature=0.2) -> str:
        self.seen_blocks.append(user)
        return json.dumps({
            "headline": "Fake headline",
            "what_happened": ["Fake claim (item_x)."],
            "when": None,
            "who_reported": [{"publisher": "P", "item_id": "item_x"}],
            "directly_supported": [],
            "uncertain": [],
            "changed_vs_earlier": None,
        })


def _video_item(session) -> str:
    source, _ = SourceRepo(session).add("https://www.youtube.com/@c", kind="youtube")
    item = make_canonical_item(
        url="https://youtu.be/x",
        title="A video about robots",
        text="Short description of the video.",
        id="item_videotest0000000001",
        source={"publisher": "Test Channel", "kind": "video", "author": "T"},
        content={"transcript": "Robots are the topic. " * 30},
        provenance={"extraction_method": "youtube",
                    "url_canonical": "https://youtu.be/x"},
    )
    outcome, row = ItemRepo(session).upsert(item, source_id=source.id)
    assert outcome == "created"
    return row.id


def test_summarize_with_llm_adapter(session, settings):
    item_id = _video_item(session)
    adapter = FakeAdapter()
    summary = summarize_item(session, settings, item_id, adapter=adapter)

    assert summary["headline"] == "Fake headline"
    assert summary["method"] == "llm:fake"
    block = adapter.seen_blocks[0]
    assert "TRANSCRIPT" in block  # transcript included in the prompt block
    stored = ItemRepo(session).get(item_id).analysis["summary"]
    assert stored["headline"] == "Fake headline"

    # cached: a second call does not hit the adapter
    n = len(adapter.seen_blocks)
    summarize_item(session, settings, item_id, adapter=adapter)
    assert len(adapter.seen_blocks) == n
    # force re-summarizes
    summarize_item(session, settings, item_id, adapter=adapter, force=True)
    assert len(adapter.seen_blocks) == n + 1


def test_summarize_without_llm_is_extractive(session, settings):
    item_id = _video_item(session)
    summary = summarize_item(session, settings, item_id)
    assert summary["method"] == "extractive"
    assert summary["uncertain"] and "extractive" in summary["uncertain"][0]
    assert summary["what_happened"]


def test_visual_description_reaches_the_prompt(session, settings):
    item_id = _video_item(session)
    ItemRepo(session).patch_analysis(item_id, {"visual": {
        "description": "A robotic arm sorts boxes.", "frames": 4}})
    adapter = FakeAdapter()
    summarize_item(session, settings, item_id, adapter=adapter, force=True)
    assert "VISUAL CONTENT" in adapter.seen_blocks[-1]


def test_digest_video_skips_media_without_config(session, settings):
    item_id = _video_item(session)
    report = digest_item(session, settings, item_id, with_vision=True)
    assert report["transcript"] == "captions"
    assert report["vision"].startswith("skipped")  # no LLM key configured
    assert report["summary"]["method"] == "extractive"
    entry = [e for e in LogRepo(session).recent(limit=10) if e.action == "item_digested"]
    assert entry and entry[0].detail["item_id"] == item_id


def test_digest_text_item_short_circuits(session, settings, energy_source):
    run_collection(session, settings, [energy_source.id])
    items = ItemRepo(session).recent(limit=1)
    assert items, "energy fixture should have items once collected"
    report = digest_item(session, settings, items[0].id)
    assert report["kind"] == "article"
    assert report["transcript"] == "n/a"


def test_audio_transcriber_against_mock_endpoint(tmp_path):
    from newsdesk.media.transcripts import AudioTranscriber
    from newsdesk.config import Settings

    settings = Settings(home=tmp_path, llm_base_url="https://llm.test/v1",
                        llm_api_key="k", llm_audio_model="whisper-test")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"text": "transcribed words"})

    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    transcriber = AudioTranscriber(settings, transport=httpx.MockTransport(handler))
    assert transcriber.transcribe_file(audio) == "transcribed words"
    assert captured["path"] == "/v1/audio/transcriptions"
    assert captured["auth"] == "Bearer k"

    with pytest.raises(Exception):
        AudioTranscriber(Settings(home=tmp_path))  # no credentials -> clear error


def test_bad_model_responses_degrade_as_llm_errors(tmp_path):
    """Null content / non-JSON 200 must surface as LLMError (the contained
    degrade path), never as a raw TypeError/ValueError that crashes the pass."""
    from newsdesk.llm.base import LLMError
    from newsdesk.media.transcripts import AudioTranscriber
    from newsdesk.media.vision import VisionDescriber
    from newsdesk.config import Settings

    settings = Settings(home=tmp_path, llm_base_url="https://llm.test/v1",
                        llm_api_key="k")

    null_content = httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            "choices": [{"message": {"content": None}}]}))
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"fake-jpeg")
    with pytest.raises(LLMError, match="null or empty"):
        VisionDescriber(settings, transport=null_content).describe_frames(
            [frame], context="x")

    not_json = httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>proxied</html>"))
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    with pytest.raises(LLMError, match="unexpected transcription"):
        AudioTranscriber(settings, transport=not_json).transcribe_file(audio)
