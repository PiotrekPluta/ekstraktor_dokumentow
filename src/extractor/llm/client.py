"""Shared LLM client interface (docs/PROJECT_NOTES.md §8 Stage 5).

Request/response types and the exception hierarchy every backend speaks —
`fake` (this stage), `llama_server`/`ollama` (Stage 5b, deferred: no model
is pinned yet, see docs/PROJECT_NOTES.md §4). Orchestration (Stage 7) only
ever holds an `LLMClient`, never a concrete backend type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMRequest:
    prompt: str
    json_schema: dict
    max_tokens: int


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int


class LLMError(Exception):
    """Base for every error `LLMClient.complete()` can raise."""


class LLMTimeout(LLMError):
    """A single request exceeded its per-request timeout."""


class LLMBackendUnavailable(LLMError):
    """The circuit breaker is open: the backend has failed too many times
    in a row this run. docs/PROJECT_NOTES.md §8 Stage 5: the run ends with
    `stop_reason=backend_unavailable` and documents stay `pending` —
    nothing is lost, a resume (a fresh client, fresh breaker) finishes them.
    """


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...
