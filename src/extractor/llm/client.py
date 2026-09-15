"""Shared LLM client interface (docs/PROJECT_NOTES.md §8 Stage 5).

Request/response types and the exception hierarchy every backend speaks —
`fake` and `llama_server` (built), `ollama` (a separate follow-up stage).
Orchestration (Stage 7) only ever holds an `LLMClient`, never a concrete
backend type.
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


class LLMConnectionError(LLMError):
    """The backend couldn't be reached or gave back something unusable:
    connection refused, DNS failure, a non-2xx status, or a response body
    that isn't valid JSON / is missing the fields a completion needs. All
    treated the same way by `ResilientLLMClient` (retryable, and it's one
    of the failures a tripped circuit breaker is watching for) — distinct
    from `LLMTimeout` only because "reachable but slow" and "not reachable
    at all" are different enough to be worth telling apart in a log.
    """


class LLMBackendUnavailable(LLMError):
    """The circuit breaker is open: the backend has failed too many times
    in a row this run. docs/PROJECT_NOTES.md §8 Stage 5: the run ends with
    `stop_reason=backend_unavailable` and documents stay `pending` —
    nothing is lost, a resume (a fresh client, fresh breaker) finishes them.
    """


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...
