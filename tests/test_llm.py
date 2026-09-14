"""LLM adapter: OpenAI-compatible transport mocked; grounding contract."""

from __future__ import annotations

import httpx
import pytest

from newsdesk.config import Settings
from newsdesk.llm.base import LLMNotConfigured, NullAdapter, get_adapter
from newsdesk.llm.openai_compat import OpenAICompatAdapter


def _adapter(response_json: dict) -> OpenAICompatAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = request.read().decode()
        assert "UNTRUSTED DATA" in body  # the system prompt must be sent
        return httpx.Response(200, json=response_json)

    return OpenAICompatAdapter(
        base_url="https://llm.test/v1",
        api_key="test-key",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )


def test_summarize_items_parses_model_json():
    model_output = (
        '{"headline": "Grid emergency", "what_happened": ["Demand hit a record"], '
        '"when": "Sunday evening", "who_reported": [{"publisher": "Example Energy Desk", '
        '"item_id": "item_x"}], "directly_supported": ["Reserve margins fell below six '
        'percent (item_x)"], "uncertain": [], "changed_vs_earlier": null}'
    )
    adapter = _adapter({"choices": [{"message": {"content": model_output}}]})
    items = [{
        "id": "item_x",
        "source": {"publisher": "Example Energy Desk", "url": "https://e.com/a",
                   "kind": "article"},
        "content": {"title": "Grid operator declares emergency",
                    "text": "Reserve margins fell below six percent.", "media": [],
                    "transcript": None},
    }]
    summary = adapter.summarize_items(items)
    assert summary["headline"] == "Grid emergency"
    assert "item_x" in summary["directly_supported"][0]


def test_unparseable_model_output_is_contained():
    adapter = _adapter({"choices": [{"message": {"content": "not json at all"}}]})
    result = adapter.summarize_items([{
        "id": "item_x", "source": {}, "content": {"title": "t", "text": "x"},
    }])
    assert result["error"] == "unparseable_model_output"
    assert "not json" in result["raw"]


def test_http_error_raises_llm_error():
    def handler(request):
        return httpx.Response(500, text="boom")

    adapter = OpenAICompatAdapter(base_url="https://llm.test/v1", api_key="k",
                                  model="m", transport=httpx.MockTransport(handler))
    with pytest.raises(Exception):
        adapter.complete("sys", "user")


def test_null_adapter_when_unconfigured(tmp_path):
    settings = Settings(home=tmp_path)
    adapter = get_adapter(settings)
    assert isinstance(adapter, NullAdapter)
    with pytest.raises(LLMNotConfigured):
        adapter.complete("sys", "user")
    result = adapter.summarize_items([{"id": "x", "source": {}, "content": {}}])
    assert result["error"] == "llm_not_configured"
