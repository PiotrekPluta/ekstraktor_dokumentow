"""The `fake` backend: config-selectable (`[backend] name = "fake"`), no
network or model — requirement 10 demands the test suite pass with
neither. Returns a canned response and counts calls, which is what
Stage 9's resumability tests need ("no repeated model calls for completed
documents"), and can be configured to fail on demand to exercise
extractor.llm.resilience's retry/circuit-breaker paths end to end.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from extractor.llm.client import LLMError, LLMRequest, LLMResponse, LLMTimeout

_DEFAULT_RESPONSE = LLMResponse(text="{}", tokens_in=1, tokens_out=1)


class FakeLLMClient:
    def __init__(
        self,
        response: LLMResponse | None = None,
        *,
        responses: list[LLMResponse] | None = None,
        fail_first_n: int = 0,
        always_fail: bool = False,
        error_factory: Callable[[], LLMError] = lambda: LLMTimeout(
            "fake backend: simulated timeout"
        ),
    ) -> None:
        """`responses`, when given, overrides `response`: successive calls
        return `responses[0]`, `responses[1]`, ... then repeat the last
        entry — Stage 7's repair-attempt path needs a client that returns
        invalid JSON once and then a valid response, without a second test
        double. `self.calls` is appended under a lock since Stage 7 shares
        one `FakeLLMClient` across worker threads (via `ResilientLLMClient`)
        the same way a real backend client would be.
        """
        self._response = response if response is not None else _DEFAULT_RESPONSE
        self._responses = responses
        self._fail_first_n = fail_first_n
        self._always_fail = always_fail
        self._error_factory = error_factory
        self.calls: list[LLMRequest] = []
        self._lock = threading.Lock()

    def complete(self, request: LLMRequest) -> LLMResponse:
        with self._lock:
            self.calls.append(request)
            call_index = len(self.calls) - 1
            should_fail = self._always_fail or len(self.calls) <= self._fail_first_n
        if should_fail:
            raise self._error_factory()
        if self._responses:
            return self._responses[min(call_index, len(self._responses) - 1)]
        return self._response
