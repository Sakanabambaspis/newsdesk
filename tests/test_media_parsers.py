"""Characterization tests for newsdesk.media (least-tested module after the
2026-09-16 audit). All offline: pure string parsing, mocked HTTP transports,
monkeypatched yt-dlp/ffmpeg seams. Docstrings lock in CURRENT behavior.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from newsdesk.config import Settings
from newsdesk.llm.base import LLMError, LLMNotConfigured
from newsdesk.media.transcripts import (AudioTranscriber, caption_text,
                                        download_audio, parse_json3, parse_vtt,
                                        pick_caption_track)
from newsdesk.media.vision import (VisionDescriber, download_video,
                                   extract_keyframes, ffmpeg_exe)

# -- caption parsers --------------------------------------------------------


def test_parse_vtt_strips_tags_and_collapses_rolling_repeats():
    vtt = (
        "WEBVTT\n"
        "Kind: captions\n"
        "Language: en\n"
        "\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "<c>Welcome back</c> to the channel.\n"
        "\n"
        "00:00:03.000 --> 00:00:05.000\n"
        "Welcome back to the channel.\n"   # rolling repeat -> dropped
        "Today: diffusion models.\n"
    )
    out = parse_vtt(vtt)
    assert out == "Welcome back to the channel.\nToday: diffusion models."


def test_parse_vtt_cue_ids_skipped_but_note_block_text_leaks_through():
    """Characterization quirk: only bare 'WEBVTT'/'NOTE' lines are skipped.
    A multi-line NOTE block ('NOTE this is a comment') is NOT recognized and
    its text passes through as caption content — a minor parser inaccuracy
    (candidates for a cleanup, recorded here not fixed)."""
    vtt = (
        "WEBVTT\n"
        "NOTE this is a comment block\n"
        "\n"
        "1\n"
        "00:00:00.000 --> 00:00:01.000\n"
        "First line\n"
        "\n"
        "2\n"
        "00:00:01.000 --> 00:00:02.000\n"
        "Second line\n"
    )
    assert parse_vtt(vtt) == "NOTE this is a comment block\nFirst line\nSecond line"


def test_parse_json3_merges_segments_and_drops_repeats():
    payload = '{"events": ['\
        '{"segs": [{"utf8": "hello "}, {"utf8": "world"}]},'\
        '{"segs": [{"utf8": "hello world"}]},'\
        '{"segs": [{"utf8": "next"}]}]}'
    assert parse_json3(payload) == "hello world\nnext"


def test_parse_json3_invalid_payload_returns_empty():
    assert parse_json3("not json at all") == ""
    assert parse_json3('{"no_events_key": true}') == ""


def test_caption_text_dispatches_by_format():
    assert caption_text("1\n00:00:01,000 --> 00:00:02,000\nHi there\n", "srt") \
        == "Hi there"          # srt is close enough to VTT for the parser
    assert caption_text('{"events":[{"segs":[{"utf8":"a"}]}]}', "json3") == "a"
    assert caption_text("1\n00:00:01,000 --> 00:00:02,000\nX\n", "vtt") == "X"


def test_pick_caption_track_prefers_language_then_format_order():
    tracks = {
        "de": [{"ext": "vtt", "url": "https://c.test/de.vtt"}],
        "en": [{"ext": "vtt", "url": "https://c.test/en.vtt"},
               {"ext": "json3", "url": "https://c.test/en.json3"}],
    }
    # en preferred; within a language json3 outranks vtt (format list order)
    assert pick_caption_track(tracks) == ("en", "https://c.test/en.json3", "json3")
    # unknown-language query order: candidates outside prefer_langs sort after
    lang, url, fmt = pick_caption_track(
        {"de": [{"ext": "srt", "url": "https://c.test/de.srt"}]})
    assert (lang, url, fmt) == ("de", "https://c.test/de.srt", "srt")


def test_pick_caption_track_returns_none_without_usable_tracks():
    assert pick_caption_track(None) is None
    assert pick_caption_track({}) is None
    assert pick_caption_track({"en": [{"ext": "ttml", "url": "https://c.test/x"}]}) is None
    assert pick_caption_track({"en": [{"ext": "vtt"}]}) is None  # no url


# -- audio transcription ------------------------------------------------------


def _settings_with_llm(home: Path) -> Settings:
    return Settings(home=home, llm_base_url="https://llm.test/v1",
                    llm_api_key="k", llm_audio_model="whisper-test")


def test_audio_transcriber_http_error_raises_llm_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")
    t = AudioTranscriber(_settings_with_llm(tmp_path),
                         transport=httpx.MockTransport(handler))
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    with pytest.raises(LLMError, match="500"):
        t.transcribe_file(audio)


def test_audio_transcriber_requires_credentials(tmp_path):
    with pytest.raises(LLMNotConfigured):
        AudioTranscriber(Settings(home=tmp_path))


def test_audio_transcriber_missing_text_field_yields_empty_string(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})
    t = AudioTranscriber(_settings_with_llm(tmp_path),
                         transport=httpx.MockTransport(handler))
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    assert t.transcribe_file(audio) == ""  # .get("text", "") — silent empty


# -- vision --------------------------------------------------------------------


def test_vision_describer_requires_credentials(tmp_path):
    with pytest.raises(LLMNotConfigured):
        VisionDescriber(Settings(home=tmp_path))


def test_vision_describer_sends_untrusted_framing_and_frames(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "A robotic arm sorts boxes."}}]})

    settings = _settings_with_llm(tmp_path)
    settings.vision_model = "vision-test"
    v = VisionDescriber(settings, transport=httpx.MockTransport(handler))
    frame = tmp_path / "frame_001.jpg"
    frame.write_bytes(b"\xff\xd8fakejpeg")
    out = v.describe_frames([frame], context="robot video")
    assert out == "A robotic arm sorts boxes."
    assert "UNTRUSTED DATA" in seen["body"]        # injection defense present
    assert "robot video" in seen["body"]           # context passed through
    assert "data:image/jpeg;base64," in seen["body"]
    assert seen["auth"] == "Bearer k"


def test_vision_describer_http_error_raises_llm_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")
    v = VisionDescriber(_settings_with_llm(tmp_path),
                        transport=httpx.MockTransport(handler))
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"x")
    with pytest.raises(LLMError, match="429"):
        v.describe_frames([frame], context="c")


def test_extract_keyframes_returns_empty_without_ffmpeg(tmp_path, monkeypatch):
    import newsdesk.media.vision as vision_mod
    monkeypatch.setattr(vision_mod, "ffmpeg_exe", lambda: None)
    assert extract_keyframes(tmp_path / "no-video.mp4", tmp_path / "out",
                             every_seconds=30.0) == []


def test_ffmpeg_exe_returns_str_or_none(monkeypatch):
    import newsdesk.media.vision as vision_mod
    monkeypatch.setattr(vision_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert ffmpeg_exe() == "/usr/bin/ffmpeg"
    monkeypatch.setattr(vision_mod.shutil, "which", lambda name: None)
    monkeypatch.setitem(__import__("sys").modules, "imageio_ffmpeg",
                        type("M", (), {"get_ffmpeg_exe": staticmethod(
                            lambda: "/wheel/ffmpeg")}))
    assert ffmpeg_exe() == "/wheel/ffmpeg"   # falls back to the static wheel
    monkeypatch.setitem(__import__("sys").modules, "imageio_ffmpeg",
                        type("M", (), {"get_ffmpeg_exe": staticmethod(
                            lambda: (_ for _ in ()).throw(RuntimeError("nope")))}))
    assert ffmpeg_exe() is None              # swallowed -> None, never raises


# -- yt-dlp download helpers ----------------------------------------------------


def test_download_audio_returns_none_when_ytdlp_fails(tmp_path, monkeypatch):
    import yt_dlp

    class Boom:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            raise RuntimeError("network down")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Boom)
    assert download_audio("https://videos.test/v1", tmp_path / "audio",
                          Settings(home=tmp_path)) is None


def test_download_video_returns_none_when_import_missing(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yt_dlp":
            raise ImportError("no yt_dlp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert download_video("https://videos.test/v1", tmp_path / "video",
                          Settings(home=tmp_path)) is None
