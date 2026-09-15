"""`llama-server` backend (docs/PROJECT_NOTES.md §8 Stage 5b) — the
primary, config-selectable backend requirement 2 needs at least two of.

Talks to `llama-server`'s native `POST /completion` (not the OpenAI-
compatible `/v1/chat/completions`): `LLMRequest.prompt` is already a flat
string, so `/completion`'s equally-flat `"prompt"` field is the natural
fit — no messages-list restructuring needed. Its `json_schema` parameter
name also matches `PROJECT_NOTES.md` §4's own wording ("llama-server takes
json_schema") more directly than `/v1/chat/completions`'s nested
`response_format.schema`. Chat-template formatting (Bielik uses ChatML) is
left to whoever builds `request.prompt` (Stage 6/7) — this client is a
transport layer, not a prompt formatter.

This client only ever talks to a server already listening on
`host:port`; starting/stopping the `llama-server` process is Stage 7/CLI's
job, not this module's.
"""

from __future__ import annotations

import httpx

from extractor.llm.client import (
    LLMConnectionError,
    LLMRequest,
    LLMResponse,
    LLMTimeout,
)

_REQUIRED_RESPONSE_FIELDS = ("content", "tokens_evaluated", "tokens_predicted")


class LlamaServerClient:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout_s: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = f"http://{host}:{port}/completion"
        self._timeout_s = timeout_s
        self._transport = transport

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload: dict = {"prompt": request.prompt, "n_predict": request.max_tokens}
        if request.json_schema:
            payload["json_schema"] = request.json_schema

        try:
            with httpx.Client(
                transport=self._transport, timeout=self._timeout_s
            ) as client:
                response = client.post(self._url, json=payload)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMTimeout(
                f"llama-server request timed out after {self._timeout_s}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMConnectionError(f"llama-server request failed: {exc}") from exc

        return _parse_response(response)

    def count_tokens(self, text: str) -> int:
        """Real, model-accurate token counting via the running server's
        `/tokenize` — the offline `tokenizer.json` alternative
        `PROJECT_NOTES.md` §8 Stage 4 also names is blocked by the base
        Bielik repo being gated on Hugging Face (scripts/fetch_runtime.py's
        module docstring). Requires a live server, unlike an offline
        tokenizer would — `extractor.windowing.build_context`'s
        `count_tokens` parameter stays optional specifically so callers
        without one running (tests, a dry pass) still get the char-proxy.
        """
        url = self._url.replace("/completion", "/tokenize")
        try:
            with httpx.Client(
                transport=self._transport, timeout=self._timeout_s
            ) as client:
                response = client.post(url, json={"content": text})
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMTimeout(
                f"llama-server /tokenize timed out after {self._timeout_s}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMConnectionError(
                f"llama-server /tokenize request failed: {exc}"
            ) from exc

        try:
            data = response.json()
            return len(data["tokens"])
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMConnectionError(
                f"llama-server /tokenize response was unusable: {response.text[:200]!r}"
            ) from exc


def _parse_response(response: httpx.Response) -> LLMResponse:
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMConnectionError(
            f"llama-server response was not valid JSON: {response.text[:200]!r}"
        ) from exc

    missing = [field for field in _REQUIRED_RESPONSE_FIELDS if field not in data]
    if missing:
        raise LLMConnectionError(
            f"llama-server response missing expected field(s) {missing}: {data!r}"
        )

    return LLMResponse(
        text=data["content"],
        tokens_in=data["tokens_evaluated"],
        tokens_out=data["tokens_predicted"],
    )
