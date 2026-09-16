"""Retry with exponential backoff, plus a circuit breaker, wrapping any
`LLMClient` (docs/PROJECT_NOTES.md §8 Stage 5). Shared across every
backend rather than reimplemented per backend — "shared interface" in the
plan means this wrapper too, not just the request/response shapes.

The breaker opens after `failure_threshold` *consecutive* failures and
stays open for the rest of this process — no half-open recovery attempt
mid-run. Once the run ends (`stop_reason=backend_unavailable`), a resume
starts a fresh client with a fresh breaker; that's the recovery mechanism,
not a timer within a single run.

Thread safety (Stage 7): one `ResilientLLMClient` is shared across every
worker thread — that's the point of a circuit breaker, a single "backend is
down" signal, rather than one breaker per worker that would each need to
independently detect the same outage. `CircuitBreaker`'s state
(`_consecutive_failures`/`_open`) is therefore guarded by a lock around each
read-modify-write (`record_failure`/`record_success`) and the `is_open`
read, so concurrent `complete()` calls from `--workers 16` can't race and
lose an increment. Retry/backoff sleeps happen outside the lock — only the
breaker's own state transitions need it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from extractor.llm.client import (
    LLMBackendUnavailable,
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
)

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_S = 1.0
DEFAULT_FAILURE_THRESHOLD = 5


class CircuitBreaker:
    def __init__(self, failure_threshold: int = DEFAULT_FAILURE_THRESHOLD) -> None:
        self._failure_threshold = failure_threshold
        self._consecutive_failures = 0
        self._open = False
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._open

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._open = True


class ResilientLLMClient:
    """Wraps `inner` behind the same `LLMClient` interface — callers never
    know retry/circuit-breaking is happening underneath.
    """

    def __init__(
        self,
        inner: LLMClient,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._inner = inner
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._breaker = CircuitBreaker(failure_threshold)
        self._sleep = sleep

    @property
    def circuit_breaker_open(self) -> bool:
        return self._breaker.is_open

    def complete(self, request: LLMRequest) -> LLMResponse:
        if self._breaker.is_open:
            raise LLMBackendUnavailable(
                "circuit breaker open: backend failed repeatedly this run"
            )

        attempt = 0
        while True:
            try:
                response = self._inner.complete(request)
            except LLMError as exc:
                self._breaker.record_failure()
                if self._breaker.is_open:
                    raise LLMBackendUnavailable(
                        "circuit breaker tripped after repeated failures"
                    ) from exc
                if attempt >= self._max_retries:
                    raise
                self._sleep(self._backoff_base_s * (2**attempt))
                attempt += 1
                continue
            else:
                self._breaker.record_success()
                return response
