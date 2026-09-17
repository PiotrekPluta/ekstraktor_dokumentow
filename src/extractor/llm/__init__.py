"""Stage 5: LLM client. `fake` (5a), `llama_server` (5b), and `ollama`
(5c) are all built and config-selectable — requirement 2's "at least two
real backends" bar is met by `llama_server` + `ollama`.
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
from extractor.llm.ollama import OllamaClient
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
    "OllamaClient",
    "ResilientLLMClient",
    "build_client",
]


def build_client(config: Config) -> LLMClient:
    """Every backend goes through the same resilience wrapper — retry and
    circuit-breaking aren't a `fake`-only concern, they're the "shared
    interface" docs/PROJECT_NOTES.md §8 Stage 5 describes.
    """
    if config.backend == "fake":
        fake_cfg = config.raw.get("backend", {}).get("fake", {})
        delay = fake_cfg.get("delay_s", 0.0)
        return ResilientLLMClient(FakeLLMClient(delay=delay))
    if config.backend == "llama_server":
        llama_cfg = config.raw["backend"]["llama_server"]
        inner = LlamaServerClient(llama_cfg["host"], llama_cfg["port"])
        return ResilientLLMClient(inner)
    if config.backend == "ollama":
        ollama_cfg = config.raw["backend"]["ollama"]
        inner = OllamaClient(
            ollama_cfg["host"], ollama_cfg["port"], ollama_cfg["model_tag"]
        )
        return ResilientLLMClient(inner)
    raise NotImplementedError(f"backend {config.backend!r} is not a known backend")
