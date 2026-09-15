"""LlamaServerClient against httpx.MockTransport — no real server, no
network, per requirement 10. The transport is injected specifically so
these tests never touch the actual pinned server binary or model.
"""

from __future__ import annotations

import json

import httpx
import pytest

from extractor.llm.client import (
    LLMBackendUnavailable,
    LLMConnectionError,
    LLMRequest,
    LLMTimeout,
)
from extractor.llm.llama_server import LlamaServerClient
from extractor.llm.resilience import ResilientLLMClient

REQUEST = LLMRequest(
    prompt="extract fields from this document", json_schema={}, max_tokens=100
)


def _client(handler) -> LlamaServerClient:
    return LlamaServerClient("127.0.0.1", 8080, transport=httpx.MockTransport(handler))


def test_successful_completion_parses_text_and_token_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/completion"
        payload = json.loads(request.read())
        assert payload["prompt"] == REQUEST.prompt
        assert payload["n_predict"] == 100
        assert "json_schema" not in payload  # empty schema must be omitted
        return httpx.Response(
            200,
            json={
                "content": '{"doc_type": "invoice"}',
                "tokens_evaluated": 42,
                "tokens_predicted": 7,
            },
        )

    client = _client(handler)
    response = client.complete(REQUEST)

    assert response.text == '{"doc_type": "invoice"}'
    assert response.tokens_in == 42
    assert response.tokens_out == 7


def test_json_schema_is_sent_when_present() -> None:
    schema = {"type": "object", "properties": {"doc_type": {"type": "string"}}}
    request = LLMRequest(prompt="x", json_schema=schema, max_tokens=50)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload["json_schema"] == schema
        return httpx.Response(
            200, json={"content": "{}", "tokens_evaluated": 1, "tokens_predicted": 1}
        )

    client = _client(handler)
    client.complete(request)


def test_timeout_raises_llm_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = _client(handler)
    with pytest.raises(LLMTimeout):
        client.complete(REQUEST)


def test_connection_error_raises_llm_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(handler)
    with pytest.raises(LLMConnectionError):
        client.complete(REQUEST)


def test_non_200_status_raises_llm_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    client = _client(handler)
    with pytest.raises(LLMConnectionError):
        client.complete(REQUEST)


def test_non_json_body_raises_llm_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all")

    client = _client(handler)
    with pytest.raises(LLMConnectionError):
        client.complete(REQUEST)


def test_missing_expected_field_raises_llm_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": "hi"})  # no token counts

    client = _client(handler)
    with pytest.raises(LLMConnectionError):
        client.complete(REQUEST)


def test_count_tokens_returns_token_list_length() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tokenize"
        payload = json.loads(request.read())
        assert payload["content"] == "hello world"
        return httpx.Response(200, json={"tokens": [1, 2, 3]})

    client = _client(handler)
    assert client.count_tokens("hello world") == 3


def test_count_tokens_unusable_response_raises_llm_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = _client(handler)
    with pytest.raises(LLMConnectionError):
        client.count_tokens("hello")


# --- integration with the resilience wrapper --------------------------------


def test_resilient_wrapper_trips_breaker_against_always_failing_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = ResilientLLMClient(
        _client(handler), max_retries=5, failure_threshold=2, sleep=lambda _s: None
    )

    with pytest.raises(LLMBackendUnavailable):
        client.complete(REQUEST)
    assert client.circuit_breaker_open
