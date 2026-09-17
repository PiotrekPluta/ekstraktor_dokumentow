from __future__ import annotations

import pytest

from extractor.config import Config
from extractor.llm import (
    LLMBackendUnavailable,
    LLMConnectionError,
    LLMError,
    LLMTimeout,
    build_client,
)
from extractor.llm.fake import FakeLLMClient
from extractor.llm.llama_server import LlamaServerClient
from extractor.llm.ollama import OllamaClient
from extractor.llm.resilience import ResilientLLMClient


def _config(backend: str, raw: dict | None = None) -> Config:
    return Config(workers=4, limit=None, budget=None, backend=backend, raw=raw or {})


def test_llm_timeout_is_an_llm_error() -> None:
    assert issubclass(LLMTimeout, LLMError)


def test_llm_backend_unavailable_is_an_llm_error() -> None:
    assert issubclass(LLMBackendUnavailable, LLMError)


def test_llm_connection_error_is_an_llm_error() -> None:
    assert issubclass(LLMConnectionError, LLMError)


def test_build_client_for_fake_backend() -> None:
    client = build_client(_config("fake"))
    assert isinstance(client, ResilientLLMClient)
    assert isinstance(client._inner, FakeLLMClient)  # testing the wiring itself


def test_build_client_for_llama_server_backend() -> None:
    raw = {"backend": {"llama_server": {"host": "127.0.0.1", "port": 8080}}}
    client = build_client(_config("llama_server", raw))
    assert isinstance(client, ResilientLLMClient)
    assert isinstance(client._inner, LlamaServerClient)  # testing the wiring itself


def test_build_client_for_ollama_backend() -> None:
    raw = {
        "backend": {
            "ollama": {
                "host": "127.0.0.1",
                "port": 11434,
                "model_tag": "speakleash/bielik-4.5b-v3.0-instruct:q8_0",
            }
        }
    }
    client = build_client(_config("ollama", raw))
    assert isinstance(client, ResilientLLMClient)
    assert isinstance(client._inner, OllamaClient)  # testing the wiring itself


def test_build_client_rejects_unknown_backend() -> None:
    with pytest.raises(NotImplementedError):
        build_client(_config("something_else"))


def test_build_client_honours_configured_llama_server_timeout() -> None:
    raw = {
        "backend": {
            "llama_server": {"host": "127.0.0.1", "port": 8080, "timeout_s": 300.0}
        }
    }
    client = build_client(_config("llama_server", raw))
    assert client._inner._timeout_s == 300.0


def test_build_client_llama_server_timeout_defaults_when_unset() -> None:
    raw = {"backend": {"llama_server": {"host": "127.0.0.1", "port": 8080}}}
    client = build_client(_config("llama_server", raw))
    assert client._inner._timeout_s == 60.0
