from __future__ import annotations

import pytest

from extractor.llm.client import LLMRequest, LLMResponse, LLMTimeout
from extractor.llm.fake import FakeLLMClient

REQUEST = LLMRequest(prompt="extract fields", json_schema={}, max_tokens=100)


def test_default_response() -> None:
    client = FakeLLMClient()
    response = client.complete(REQUEST)
    assert response.text == "{}"
    assert response.tokens_in == 1
    assert response.tokens_out == 1


def test_custom_response() -> None:
    canned = LLMResponse(text='{"doc_type": "invoice"}', tokens_in=42, tokens_out=7)
    client = FakeLLMClient(canned)
    assert client.complete(REQUEST) == canned


def test_records_every_call() -> None:
    client = FakeLLMClient()
    other_request = LLMRequest(prompt="different prompt", json_schema={}, max_tokens=50)

    client.complete(REQUEST)
    client.complete(other_request)

    assert client.calls == [REQUEST, other_request]


def test_fail_first_n_then_succeeds() -> None:
    client = FakeLLMClient(fail_first_n=2)

    with pytest.raises(LLMTimeout):
        client.complete(REQUEST)
    with pytest.raises(LLMTimeout):
        client.complete(REQUEST)
    response = client.complete(REQUEST)

    assert response.text == "{}"
    assert len(client.calls) == 3


def test_always_fail() -> None:
    client = FakeLLMClient(always_fail=True)
    for _ in range(5):
        with pytest.raises(LLMTimeout):
            client.complete(REQUEST)
    assert len(client.calls) == 5


def test_custom_error_factory() -> None:
    class _CustomError(LLMTimeout):
        pass

    client = FakeLLMClient(always_fail=True, error_factory=_CustomError)
    with pytest.raises(_CustomError):
        client.complete(REQUEST)


def test_failing_calls_are_still_recorded() -> None:
    client = FakeLLMClient(fail_first_n=1)
    with pytest.raises(LLMTimeout):
        client.complete(REQUEST)
    assert len(client.calls) == 1
