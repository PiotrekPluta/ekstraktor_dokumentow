"""The `fake` backend: config-selectable (`[backend] name = "fake"`), no
network or model — requirement 10 demands the test suite pass with
neither. Returns a canned response and counts calls, which is what
Stage 9's resumability tests need ("no repeated model calls for completed
documents"), and can be configured to fail on demand to exercise
extractor.llm.resilience's retry/circuit-breaker paths end to end.
"""

from __future__ import annotations

from collections.abc import Callable

from extractor.llm.client import LLMError, LLMRequest, LLMResponse, LLMTimeout

_DEFAULT_RESPONSE = LLMResponse(text="{}", tokens_in=1, tokens_out=1)


class FakeLLMClient:
    def __init__(
        self,
        response: LLMResponse | None = None,
        *,
        fail_first_n: int = 0,
        always_fail: bool = False,
        error_factory: Callable[[], LLMError] = lambda: LLMTimeout(
            "fake backend: simulated timeout"
        ),
    ) -> None:
        self._response = response if response is not None else _DEFAULT_RESPONSE
        self._fail_first_n = fail_first_n
        self._always_fail = always_fail
        self._error_factory = error_factory
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if self._always_fail or len(self.calls) <= self._fail_first_n:
            raise self._error_factory()
        return self._response
