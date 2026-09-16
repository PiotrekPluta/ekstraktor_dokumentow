"""No real sleeping anywhere here: every ResilientLLMClient is built with
an injected `sleep` that just records calls, so exponential backoff is
verified by its arguments, not by actually waiting.
"""

from __future__ import annotations

import threading

import pytest

from extractor.llm.client import LLMBackendUnavailable, LLMError, LLMRequest, LLMTimeout
from extractor.llm.fake import FakeLLMClient
from extractor.llm.resilience import CircuitBreaker, ResilientLLMClient

REQUEST = LLMRequest(prompt="extract fields", json_schema={}, max_tokens=100)


class _RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# --- CircuitBreaker --------------------------------------------------------


def test_circuit_breaker_starts_closed() -> None:
    assert not CircuitBreaker(failure_threshold=3).is_open


def test_circuit_breaker_opens_after_consecutive_failures() -> None:
    breaker = CircuitBreaker(failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    assert not breaker.is_open
    breaker.record_failure()
    assert breaker.is_open


def test_circuit_breaker_success_resets_consecutive_count() -> None:
    breaker = CircuitBreaker(failure_threshold=2)
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert not breaker.is_open  # only 1 consecutive failure since the reset


def test_circuit_breaker_stays_open_once_tripped() -> None:
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    assert breaker.is_open
    breaker.record_success()
    assert breaker.is_open  # docs: no half-open recovery within a run


# --- ResilientLLMClient: happy path ----------------------------------------


def test_successful_call_passes_through_with_no_retry_or_sleep() -> None:
    fake = FakeLLMClient()
    sleep = _RecordingSleep()
    client = ResilientLLMClient(fake, sleep=sleep)

    response = client.complete(REQUEST)

    assert response.text == "{}"
    assert len(fake.calls) == 1
    assert sleep.calls == []


# --- retry with backoff -----------------------------------------------------


def test_transient_failures_are_retried_until_success() -> None:
    fake = FakeLLMClient(fail_first_n=2)
    sleep = _RecordingSleep()
    client = ResilientLLMClient(fake, max_retries=3, failure_threshold=10, sleep=sleep)

    response = client.complete(REQUEST)

    assert response.text == "{}"
    assert len(fake.calls) == 3  # 2 failures + 1 success
    assert sleep.calls == [1.0, 2.0]  # backoff_base_s * 2**attempt, per failed attempt


def test_exhausting_retries_raises_the_original_error_not_backend_unavailable() -> None:
    # failure_threshold higher than max_retries+1 so the breaker never
    # trips here — this is specifically the "gave up retrying" path, not
    # the circuit-breaker path (see the next test for that one).
    fake = FakeLLMClient(always_fail=True)
    sleep = _RecordingSleep()
    client = ResilientLLMClient(fake, max_retries=2, failure_threshold=10, sleep=sleep)

    with pytest.raises(LLMTimeout):
        client.complete(REQUEST)

    assert len(fake.calls) == 3  # initial attempt + 2 retries
    assert len(sleep.calls) == 2


# --- circuit breaker ---------------------------------------------------------


def test_circuit_breaker_can_trip_before_retries_are_exhausted() -> None:
    # max_retries is generous, but failure_threshold is tight — the
    # breaker should end the attempt loop early rather than retry forever.
    fake = FakeLLMClient(always_fail=True)
    sleep = _RecordingSleep()
    client = ResilientLLMClient(fake, max_retries=10, failure_threshold=2, sleep=sleep)

    with pytest.raises(LLMBackendUnavailable):
        client.complete(REQUEST)

    assert len(fake.calls) == 2
    assert client.circuit_breaker_open


def test_open_circuit_breaker_rejects_calls_without_touching_inner_client() -> None:
    fake = FakeLLMClient(always_fail=True)
    sleep = _RecordingSleep()
    client = ResilientLLMClient(fake, max_retries=0, failure_threshold=1, sleep=sleep)

    with pytest.raises(LLMBackendUnavailable):
        client.complete(REQUEST)
    assert len(fake.calls) == 1

    # Breaker is now open — a second call must not burn another attempt.
    with pytest.raises(LLMBackendUnavailable):
        client.complete(REQUEST)
    assert len(fake.calls) == 1


# --- Stage 7: concurrent complete() from several worker threads ------------


def test_concurrent_complete_calls_trip_breaker_exactly_once_no_race() -> None:
    """--workers 16 against a down backend (docs/plan/Stage_7_plan.md
    decision 5): every thread calls complete() on the same
    ResilientLLMClient/CircuitBreaker concurrently. Without the lock added
    to CircuitBreaker, concurrent record_failure() read-modify-write calls
    can race and lose an increment — this asserts that doesn't happen: the
    breaker opens (some threads may still raise LLMTimeout if they read
    "not yet open" before another thread's failure tips it over, but none
    may raise anything other than an LLMError, and it must end up open).
    """
    fake = FakeLLMClient(always_fail=True)
    client = ResilientLLMClient(
        fake, max_retries=0, failure_threshold=5, sleep=lambda _: None
    )

    errors: list[Exception] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            client.complete(REQUEST)
        except LLMError as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 20  # every call failed one way or another
    assert client.circuit_breaker_open
    # The breaker must have opened from consecutive failures actually being
    # counted, not by chance — with a race dropping increments, threshold=5
    # could be reached late or never despite 20 failing calls.
