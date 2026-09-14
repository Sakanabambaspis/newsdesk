"""OpenAI-compatible chat-completions adapter.

Works with any endpoint that speaks the /chat/completions shape:
OpenAI, GLM, DeepSeek, Ollama (with OpenAI-compat mode), vLLM, etc.
"""

from __future__ import annotations

import httpx

from .base import BaseLLMAdapter, LLMError


class OpenAICompatAdapter(BaseLLMAdapter):
    name = "openai-compat"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None):
        self.model = model
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 temperature: float = 0.2) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            response = self.client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        if response.status_code >= 400:
            raise LLMError(
                f"LLM returned HTTP {response.status_code}: {response.text[:300]}"
            )
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response shape: {str(data)[:300]}") from exc
