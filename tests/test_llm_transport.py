"""Characterization tests for the LLM adapter transport layer (llm/base,
llm/openai_compat) — the paths test_llm.py does not cover: error shapes,
the digest prompt path, truncation caps, and adapter selection.
All offline via httpx.MockTransport.
"""

from __future__ import annotations

import json

import httpx
import pytest

from newsdesk.config import Settings
from newsdesk.llm.base import (BaseLLMAdapter, LLMError, LLMNotConfigured,
                               NullAdapter, get_adapter)
from newsdesk.llm.openai_compat import OpenAICompatAdapter


def _adapter(handler) -> OpenAICompatAdapter:
    return OpenAICompatAdapter(base_url="https://llm.test/v1", api_key="k",
                               model="m", transport=httpx.MockTransport(handler))


def _canonical_item(text: str) -> dict:
    return {"id": "item_x", "source": {"publisher": "P", "url": "u",
                                       "kind": "article"},
            "content": {"title": "T", "text": text, "media": [],
                        "transcript": None}}


# -- transport error handling ---------------------------------------------------


def test_http_401_maps_to_llm_error():
    def handler(request):
        return httpx.Response(401, text="bad key")
    with pytest.raises(LLMError, match="401"):
        _adapter(handler).complete("sys", "user")


# -- transient-failure retries (free-tier upstream blips) ------------------------


def test_429_then_200_retries_and_succeeds(monkeypatch):
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) <= 2:
            return httpx.Response(429, json={"error": {"code": 429}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("newsdesk.llm.openai_compat.time.sleep",
                        lambda s: sleeps.append(s))
    assert _adapter(handler).complete("sys", "user") == "ok"
    assert len(calls) == 3
    assert sleeps == [5.0, 15.0]


def test_persistent_429_raises_after_all_retries(monkeypatch):
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={"error": {"code": 429}})

    monkeypatch.setattr("newsdesk.llm.openai_compat.time.sleep",
                        lambda s: sleeps.append(s))
    with pytest.raises(LLMError, match="429"):
        _adapter(handler).complete("sys", "user")
    assert len(calls) == 4                     # initial + 3 retries
    assert sleeps == [5.0, 15.0, 45.0]


def test_401_fails_fast_without_retry(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="bad key")

    monkeypatch.setattr("newsdesk.llm.openai_compat.time.sleep",
                        lambda s: calls.append("slept"))
    with pytest.raises(LLMError, match="401"):
        _adapter(handler).complete("sys", "user")
    assert calls == [1]                        # no retry, no sleep


def test_connect_error_maps_to_llm_error():
    def handler(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(LLMError, match="LLM request failed"):
        _adapter(handler).complete("sys", "user")


def test_wrong_response_shape_maps_to_llm_error():
    def handler(request):
        return httpx.Response(200, json={"choices": []})  # empty choices
    with pytest.raises(LLMError, match="unexpected LLM response shape"):
        _adapter(handler).complete("sys", "user")


def test_200_with_non_json_body_raises_raw_json_decode_error():
    """Characterization (new, recorded in batch-2 notes, code untouched):
    complete() guards the request and the status code, but response.json()
    on a 200 with a non-JSON body raises a bare json.JSONDecodeError instead
    of LLMError — so summarize_item's `except LLMError` containment (DESIGN
    section 8 'unparseable output is stored as a contained error') does not
    cover a server that answers 200 text/html."""
    def handler(request):
        return httpx.Response(200, text="<html>gateway splash</html>",
                              headers={"content-type": "text/html"})
    with pytest.raises(json.JSONDecodeError):
        _adapter(handler).complete("sys", "user")


def test_request_carries_auth_header_model_and_messages():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.read().decode())
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}]})

    out = _adapter(handler).complete("sys", "user", max_tokens=77, temperature=0.0)
    assert out == "ok"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "m"
    assert seen["body"]["max_tokens"] == 77
    assert seen["body"]["temperature"] == 0.0
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]


# -- prompt building (summarize_items / summarize_digest) ------------------------


def test_summarize_items_caps_transcript_and_text_lengths():
    seen = {}

    class Spy(BaseLLMAdapter):
        name = "spy"

        def complete(self, system, user, *, max_tokens=1200, temperature=0.2):
            seen["system"], seen["user"] = system, user
            return '{"headline": "h"}'

    item = _canonical_item("w" * 10000)
    item["content"]["transcript"] = "t" * 20000
    out = Spy().summarize_items([item])
    assert out == {"headline": "h"}
    assert '"text"' not in seen["user"]
    assert "w" * 4000 in seen["user"] and "w" * 4001 not in seen["user"]
    assert "t" * 12000 in seen["user"] and "t" * 12001 not in seen["user"]


def test_summarize_digest_tags_items_with_relevance_and_matched_terms():
    seen = {}

    class Spy(BaseLLMAdapter):
        name = "spy"

        def complete(self, system, user, *, max_tokens=1200, temperature=0.2):
            seen["user"] = user
            return '{"overview": "o"}'

    items = [{"id": "item_1", "publisher": "Pub", "title": "Headline",
              "text": "body text", "snippet": "body text",
              "relevance": 0.75, "matched_terms": ["grid"]}]
    out = Spy().summarize_digest(items)
    assert out == {"overview": "o"}
    assert 'id="item_1"' in seen["user"]
    assert 'publisher="Pub"' in seen["user"]
    assert 'relevance="0.75"' in seen["user"]
    assert 'matched="grid"' in seen["user"]
    # (the user block is pure data; the UNTRUSTED framing is asserted on the
    # system prompt in the next test)


def test_summarize_digest_system_prompt_carries_injection_defense():
    seen = {}

    class Spy(BaseLLMAdapter):
        name = "spy"

        def complete(self, system, user, *, max_tokens=1200, temperature=0.2):
            seen["system"] = system
            return '{"overview": "o"}'

    Spy().summarize_digest([{"id": "i", "title": "", "snippet": "",
                             "publisher": "", "relevance": 0.0,
                             "matched_terms": []}])
    assert "UNTRUSTED DATA" in seen["system"]
    assert "every item_id you cite" in seen["system"]  # citation rule present


def test_summarize_items_empty_input_short_circuits():
    class Spy(BaseLLMAdapter):
        name = "spy"

        def complete(self, system, user, *, max_tokens=1200, temperature=0.2):
            raise AssertionError("must not call the model for empty input")

    assert Spy().summarize_items([]) == {"error": "no_items"}
    assert Spy().summarize_digest([]) == {"error": "no_items"}


# -- null adapter + selection ------------------------------------------------------


def test_null_adapter_digest_delegates_to_items_error():
    """Characterization quirk: NullAdapter overrides summarize_items WITHOUT
    BaseLLMAdapter's no_items guard, so empty input still answers
    'llm_not_configured' (not 'no_items') — digest.py handles either error
    key with its extractive fallback, so behavior is consistent end to end."""
    null = NullAdapter()
    assert null.summarize_digest([{"id": "x"}])["error"] == "llm_not_configured"
    assert null.summarize_digest([]) == {
        "error": "llm_not_configured",
        "hint": "set NEWSDESK_LLM_BASE_URL and NEWSDESK_LLM_API_KEY"}
    assert null.summarize_items([])["error"] == "llm_not_configured"
    with pytest.raises(LLMNotConfigured):
        null.complete("sys", "user")


def test_get_adapter_selects_by_configuration(tmp_path):
    s = Settings(home=tmp_path)
    assert isinstance(get_adapter(s), NullAdapter)
    s.llm_base_url = "https://llm.test/v1"   # key still missing -> null
    assert isinstance(get_adapter(s), NullAdapter)
    s.llm_api_key = "k"
    adapter = get_adapter(s)
    assert isinstance(adapter, OpenAICompatAdapter)
    assert adapter.model == "gpt-4o-mini"    # Settings default model
