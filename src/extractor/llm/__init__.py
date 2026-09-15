"""Stage 5: LLM client. `fake` (5a) and `llama_server` (5b) are built;
`ollama` is a separate follow-up stage — `build_client` raises
`NotImplementedError` for it until then.
"""

from __future__ import annotations

from extractor.config import Config
from extractor.llm.client import (
    LLMBackendUnavailable,
    LLMClient,
    LLMConnectionError,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMTimeout,
)
from extractor.llm.fake import FakeLLMClient
from extractor.llm.llama_server import LlamaServerClient
from extractor.llm.resilience import CircuitBreaker, ResilientLLMClient

__all__ = [
    "CircuitBreaker",
    "FakeLLMClient",
    "LLMBackendUnavailable",
    "LLMClient",
    "LLMConnectionError",
    "LLMError",
    "LLMRequest",
    "LLMResponse",
    "LLMTimeout",
    "LlamaServerClient",
    "ResilientLLMClient",
    "build_client",
]


def build_client(config: Config) -> LLMClient:
    """Every backend goes through the same resilience wrapper — retry and
    circuit-breaking aren't a `fake`-only concern, they're the "shared
    interface" docs/PROJECT_NOTES.md §8 Stage 5 describes.
    """
    if config.backend == "fake":
        return ResilientLLMClient(FakeLLMClient())
    if config.backend == "llama_server":
        llama_cfg = config.raw["backend"]["llama_server"]
        inner = LlamaServerClient(llama_cfg["host"], llama_cfg["port"])
        return ResilientLLMClient(inner)
    raise NotImplementedError(
        f"backend {config.backend!r} is not wired yet — ollama is a separate "
        "follow-up stage"
    )
