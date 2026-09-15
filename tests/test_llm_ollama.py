"""OllamaClient against httpx.MockTransport — no real daemon, no network,
per requirement 10.
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
from extractor.llm.ollama import OllamaClient
from extractor.llm.resilience import ResilientLLMClient

REQUEST = LLMRequest(
    prompt="extract fields from this document", json_schema={}, max_tokens=100
)


def _client(handler) -> OllamaClient:
    return OllamaClient(
        "127.0.0.1",
        11434,
        "bielik-4.5b-v3.0-instruct:q8_0",
        transport=httpx.MockTransport(handler),
    )


def test_successful_generate_parses_text_and_token_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        payload = json.loads(request.read())
        assert payload["model"] == "bielik-4.5b-v3.0-instruct:q8_0"
        assert payload["prompt"] == REQUEST.prompt
        assert payload["stream"] is False
        assert payload["options"]["num_predict"] == 100
        assert "format" not in payload  # empty schema must be omitted
        return httpx.Response(
            200,
            json={
                "response": '{"doc_type": "invoice"}',
                "prompt_eval_count": 42,
                "eval_count": 7,
                "done": True,
            },
        )

    client = _client(handler)
    response = client.complete(REQUEST)

    assert response.text == '{"doc_type": "invoice"}'
    assert response.tokens_in == 42
    assert response.tokens_out == 7


def test_json_schema_is_sent_as_format_when_present() -> None:
    schema = {"type": "object", "properties": {"doc_type": {"type": "string"}}}
    request = LLMRequest(prompt="x", json_schema=schema, max_tokens=50)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert payload["format"] == schema
        return httpx.Response(
            200, json={"response": "{}", "prompt_eval_count": 1, "eval_count": 1}
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
        return httpx.Response(200, json={"response": "hi"})  # no token counts

    client = _client(handler)
    with pytest.raises(LLMConnectionError):
        client.complete(REQUEST)


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
