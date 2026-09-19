"""OpenAI-compatible chat-completions adapter.

Works with any endpoint that speaks the /chat/completions shape:
OpenAI, GLM, DeepSeek, Ollama (with OpenAI-compat mode), vLLM, etc.

Two layers of resilience, tuned for free endpoints:
- per model, transient statuses (429/502/503) are retried with a short
  backoff — a capacity blip is worth waiting out;
- if a model still fails, the next model in the fallback chain answers
  instead, so one provider's bad hour costs nothing. The aggregate
  error names every model's cause (log-safe by contract).
"""

from __future__ import annotations

import time

import httpx

from .base import BaseLLMAdapter, LLMError

RETRY_STATUS = frozenset({429, 502, 503})
RETRY_DELAYS_S = (5.0, 15.0, 45.0)
_REASON_CAP = 400


class OpenAICompatAdapter(BaseLLMAdapter):
    name = "openai-compat"

    def __init__(self, base_url: str, api_key: str, model: str,
                 fallback_models: list[str] | None = None,
                 timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None,
                 extra_payload: dict | None = None):
        self.model = model
        self.models = [model, *(m.strip() for m in (fallback_models or [])
                                if m.strip())]
        self.extra_payload = extra_payload or {}
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 temperature: float = 0.2) -> str:
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            **self.extra_payload,
        }
        failures: list[str] = []
        for model in self.models:
            payload["model"] = model
            try:
                return self._complete_once(payload)
            except LLMError as exc:
                failures.append(f"{model}: {str(exc)[:_REASON_CAP]}")
        raise LLMError(
            f"all {len(self.models)} model(s) failed — " + "; ".join(failures)
        )

    def _complete_once(self, payload: dict) -> str:
        """One model, with transient-status retries. Raises LLMError with a
        log-safe reason (the digest report surfaces it verbatim)."""
        for attempt in range(len(RETRY_DELAYS_S) + 1):
            try:
                response = self.client.post("/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                raise LLMError(f"LLM request failed: {exc}") from exc
            if response.status_code in RETRY_STATUS and attempt < len(RETRY_DELAYS_S):
                time.sleep(RETRY_DELAYS_S[attempt])
                continue
            break
        if response.status_code >= 400:
            raise LLMError(
                f"LLM returned HTTP {response.status_code}: "
                f"{response.text[:_REASON_CAP]}"
            )
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"unexpected LLM response shape: {str(exc)[:_REASON_CAP]}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            # reasoning-tuned models answer with content: null and put the
            # text in a thinking field — contained failure, never None
            raise LLMError("model returned null or empty content")
        return content
