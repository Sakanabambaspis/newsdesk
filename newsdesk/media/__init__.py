"""Media processing: caption/transcript extraction, audio transcription, vision.

Each capability is an adapter behind a small function/class interface so the
pipeline stays transport-agnostic: captions arrive free with the video,
audio transcription and vision descriptions go through the configured
OpenAI-compatible endpoint, and everything degrades to None when the
dependency or credential is missing.
"""
