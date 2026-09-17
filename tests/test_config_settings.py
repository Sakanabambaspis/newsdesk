"""Characterization tests for newsdesk.config — every env knob the code
actually reads (companion to docs/reviews/config-audit-20260917.md) plus
Settings behaviors the docs don't spell out. Offline; env via monkeypatch.
"""

from __future__ import annotations

from pathlib import Path

from newsdesk.config import DEFAULT_USER_AGENT, Settings


def test_defaults_are_local_first(tmp_path):
    s = Settings(home=tmp_path)
    assert s.user_agent == DEFAULT_USER_AGENT
    assert "NewsdeskBot" in s.user_agent and "robots.txt" in s.user_agent
    assert s.request_timeout == 20.0
    assert s.min_request_interval == 5.0          # politeness: 5s per host
    assert s.max_retries == 2
    assert s.max_items_per_feed == 200
    assert s.llm_base_url is None and s.llm_api_key is None
    assert s.llm_model is None  # dataclass default; the "gpt-4o-mini" default
    # is applied only by from_env() (get_adapter re-applies it as a fallback)
    s2 = Settings.from_env()
    assert s2.llm_model == "gpt-4o-mini"
    assert s.llm_audio_model == "whisper-1"
    assert s.vision_model is None                 # falls back to llm_model in code
    assert s.transcribe_audio is False            # costly: opt-in
    assert s.keyframe_interval == 30.0


def test_database_url_defaults_to_sqlite_in_home(tmp_path):
    s = Settings(home=tmp_path)
    assert s.database_url == f"sqlite:///{(tmp_path / 'newsdesk.db').as_posix()}"
    s.db_url = "postgresql://n:n@localhost/n"
    assert s.database_url == "postgresql://n:n@localhost/n"  # Postgres path (section 13)


def test_ensure_dirs_creates_home_and_snapshots_but_not_media(tmp_path):
    s = Settings(home=tmp_path / "deep" / "home")
    s.ensure_dirs()
    assert (tmp_path / "deep" / "home" / "snapshots").is_dir()
    assert not (tmp_path / "deep" / "home" / "media").exists()  # created lazily


def test_from_env_reads_every_documented_knob(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSDESK_HOME", str(tmp_path))
    monkeypatch.setenv("NEWSDESK_DB_URL", "sqlite:///custom.db")
    monkeypatch.setenv("NEWSDESK_USER_AGENT", "CustomAgent/1.0")
    monkeypatch.setenv("NEWSDESK_REQUEST_TIMEOUT", "7.5")
    monkeypatch.setenv("NEWSDESK_MIN_INTERVAL", "3")
    monkeypatch.setenv("NEWSDESK_LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("NEWSDESK_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("NEWSDESK_LLM_MODEL", "glm-5")
    monkeypatch.setenv("NEWSDESK_LLM_AUDIO_MODEL", "whisper-large")
    monkeypatch.setenv("NEWSDESK_VISION_MODEL", "vision-x")
    monkeypatch.setenv("NEWSDESK_TRANSCRIBE_AUDIO", "1")
    monkeypatch.setenv("NEWSDESK_KEYFRAME_INTERVAL", "15")

    s = Settings.from_env()
    assert s.home == tmp_path
    assert s.db_url == "sqlite:///custom.db"
    assert s.user_agent == "CustomAgent/1.0"
    assert s.request_timeout == 7.5
    assert s.min_request_interval == 3.0
    assert s.llm_base_url == "https://llm.test/v1"
    assert s.llm_api_key == "sk-test"
    assert s.llm_model == "glm-5"
    assert s.llm_audio_model == "whisper-large"
    assert s.vision_model == "vision-x"
    assert s.transcribe_audio is True
    assert s.keyframe_interval == 15.0


def test_openai_env_fallbacks(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://oai-proxy.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    s = Settings.from_env()
    assert s.llm_base_url == "https://oai-proxy.test/v1"
    assert s.llm_api_key == "sk-oai"
    # NEWSDESK_* wins when both are set
    monkeypatch.setenv("NEWSDESK_LLM_BASE_URL", "https://glm.test/v1")
    assert Settings.from_env().llm_base_url == "https://glm.test/v1"


def test_transcribe_audio_truthy_parse(tmp_path, monkeypatch):
    monkeypatch.delenv("NEWSDESK_TRANSCRIBE_AUDIO", raising=False)
    assert Settings.from_env().transcribe_audio is False
    for truthy in ("1", "true", "yes", "TRUE", "Yes"):
        monkeypatch.setenv("NEWSDESK_TRANSCRIBE_AUDIO", truthy)
        assert Settings.from_env().transcribe_audio is True
    monkeypatch.setenv("NEWSDESK_TRANSCRIBE_AUDIO", "on")  # undocumented value
    assert Settings.from_env().transcribe_audio is False   # only 1/true/yes count


def test_home_defaults_to_dot_newsdesk(monkeypatch):
    monkeypatch.delenv("NEWSDESK_HOME", raising=False)
    s = Settings.from_env()
    assert s.home == Path.home() / ".newsdesk"
