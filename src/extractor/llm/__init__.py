"""Stage 5: LLM client. `fake` backend built now (5a); `llama_server`/
`ollama` backends and the actual model pin are 5b, deliberately deferred —
no model is chosen yet (docs/PROJECT_NOTES.md §4).
"""

from __future__ import annotations

from extractor.config import Config
from extractor.llm.client import (
    LLMBackendUnavailable,
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMTimeout,
)
from extractor.llm.fake import FakeLLMClient
from extractor.llm.resilience import CircuitBreaker, ResilientLLMClient

__all__ = [
    "CircuitBreaker",
    "FakeLLMClient",
    "LLMBackendUnavailable",
    "LLMClient",
    "LLMError",
    "LLMRequest",
    "LLMResponse",
    "LLMTimeout",
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
    raise NotImplementedError(
        f"backend {config.backend!r} is not wired yet — llama_server/ollama "
        "are Stage 5b (no model pinned yet, see docs/PROJECT_NOTES.md §4)"
    )
