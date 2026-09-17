"""Runtime configuration, loaded from environment variables with local-first defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_USER_AGENT = "NewsdeskBot/0.1 (local research agent; respects robots.txt)"


@dataclass
class Settings:
    """All runtime knobs. Local single-user defaults; Postgres/remote LLM drop in via env."""

    home: Path
    db_url: str | None = None
    user_agent: str = DEFAULT_USER_AGENT
    request_timeout: float = 20.0
    min_request_interval: float = 5.0
    max_retries: int = 2
    max_items_per_feed: int = 200
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    feed_token: str | None = None  # the morning feed's only auth (path segment)
    feed_base_url: str | None = None  # public host base (e.g. https://x.pages.dev)
    feed_owner_email: str | None = None  # alias for Apple's required owner email
    llm_audio_model: str = "whisper-1"  # OpenAI-compatible /audio/transcriptions model
    vision_model: str | None = None  # defaults to llm_model; must accept image inputs
    transcribe_audio: bool = False  # download+transcribe caption-less audio (costly)
    keyframe_interval: float = 30.0  # seconds between extracted keyframes
    morning_voice: str = "en-US-AriaNeural"  # all-English script (impl ticket 08)
    morning_voice_alt: str = "en-US-EmmaMultilingualNeural"  # per-chunk fallback
    tts_retries: int = 4  # 4 retries + first try = 5 attempts per voice
    tts_pace: float = 1.5  # seconds between chunk requests (research band: 1–3 s)

    @property
    def snapshots_dir(self) -> Path:
        return self.home / "snapshots"

    @property
    def media_dir(self) -> Path:
        return self.home / "media"

    @property
    def morning_dir(self) -> Path:
        return self.home / "morning"

    @property
    def publish_dir(self) -> Path:
        return self.morning_dir / "publish"

    @property
    def database_url(self) -> str:
        if self.db_url:
            return self.db_url
        return f"sqlite:///{(self.home / 'newsdesk.db').as_posix()}"

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Settings":
        home = Path(os.environ.get("NEWSDESK_HOME", str(Path.home() / ".newsdesk")))
        return cls(
            home=home,
            db_url=os.environ.get("NEWSDESK_DB_URL"),
            user_agent=os.environ.get("NEWSDESK_USER_AGENT", DEFAULT_USER_AGENT),
            request_timeout=float(os.environ.get("NEWSDESK_REQUEST_TIMEOUT", "20")),
            min_request_interval=float(os.environ.get("NEWSDESK_MIN_INTERVAL", "5")),
            llm_base_url=(
                os.environ.get("NEWSDESK_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
            ),
            llm_api_key=(
                os.environ.get("NEWSDESK_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
            ),
            llm_model=os.environ.get("NEWSDESK_LLM_MODEL", "gpt-4o-mini"),
            feed_token=os.environ.get("NEWSDESK_FEED_TOKEN"),
            feed_base_url=os.environ.get("NEWSDESK_FEED_BASE_URL"),
            feed_owner_email=os.environ.get("NEWSDESK_FEED_OWNER_EMAIL"),
            llm_audio_model=os.environ.get("NEWSDESK_LLM_AUDIO_MODEL", "whisper-1"),
            vision_model=os.environ.get("NEWSDESK_VISION_MODEL"),
            transcribe_audio=os.environ.get("NEWSDESK_TRANSCRIBE_AUDIO", "").lower()
            in ("1", "true", "yes"),
            keyframe_interval=float(os.environ.get("NEWSDESK_KEYFRAME_INTERVAL", "30")),
            morning_voice=os.environ.get("NEWSDESK_MORNING_VOICE", "en-US-AriaNeural"),
            morning_voice_alt=os.environ.get(
                "NEWSDESK_MORNING_VOICE_ALT", "en-US-EmmaMultilingualNeural"),
            tts_retries=int(os.environ.get("NEWSDESK_TTS_RETRIES", "4")),
            tts_pace=float(os.environ.get("NEWSDESK_TTS_PACE", "1.5")),
        )
