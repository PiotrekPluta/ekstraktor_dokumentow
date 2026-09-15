from __future__ import annotations

import pytest

from extractor.config import Config
from extractor.llm import LLMBackendUnavailable, LLMError, LLMTimeout, build_client
from extractor.llm.fake import FakeLLMClient
from extractor.llm.resilience import ResilientLLMClient


def _config(backend: str) -> Config:
    return Config(workers=4, limit=None, budget=None, backend=backend, raw={})


def test_llm_timeout_is_an_llm_error() -> None:
    assert issubclass(LLMTimeout, LLMError)


def test_llm_backend_unavailable_is_an_llm_error() -> None:
    assert issubclass(LLMBackendUnavailable, LLMError)


def test_build_client_for_fake_backend() -> None:
    client = build_client(_config("fake"))
    assert isinstance(client, ResilientLLMClient)
    assert isinstance(client._inner, FakeLLMClient)  # testing the wiring itself


@pytest.mark.parametrize("backend", ["llama_server", "ollama", "something_else"])
def test_build_client_rejects_unwired_backends(backend: str) -> None:
    with pytest.raises(NotImplementedError, match=backend):
        build_client(_config(backend))
