"""Ollama backend (docs/PROJECT_NOTES.md §8 Stage 5, "5c" — the second
config-selectable backend requirement 2 needs at least two of).

Talks to `POST /api/generate` with `"stream": false` — Ollama's endpoint
streams NDJSON by default, and this client, like `LlamaServerClient`,
wants one complete JSON object per call, not a chunked response to
reassemble. `json_schema` goes in Ollama's `format` field (its structured
-output parameter — PROJECT_NOTES.md §4: "Ollama takes a JSON Schema in
format"), the mirror of llama-server's own `json_schema` parameter.

No `count_tokens` here, unlike `LlamaServerClient`: Ollama exposes no
`/tokenize`-equivalent, but it doesn't need one — `extractor.tokenizer`'s
offline counting is backend-agnostic (it's the same Bielik tokenizer
regardless of which server runs it), so it already covers this backend
too. `LlamaServerClient.count_tokens` exists only because llama-server
*does* expose `/tokenize` and it's a reasonable fallback if `assets/`
tokenizer.json is ever unavailable.

Like `LlamaServerClient`, this only ever talks to a server (the Ollama
daemon) already running with the pinned model already pulled — process
and model lifecycle belong to Stage 7/CLI, not this module.
"""

from __future__ import annotations

import httpx

from extractor.llm.client import (
    LLMConnectionError,
    LLMRequest,
    LLMResponse,
    LLMTimeout,
)

_REQUIRED_RESPONSE_FIELDS = ("response", "prompt_eval_count", "eval_count")


class OllamaClient:
    def __init__(
        self,
        host: str,
        port: int,
        model: str,
        *,
        timeout_s: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = f"http://{host}:{port}/api/generate"
        self._model = model
        self._timeout_s = timeout_s
        self._transport = transport

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "stream": False,
            "options": {"num_predict": request.max_tokens},
        }
        if request.json_schema:
            payload["format"] = request.json_schema

        try:
            with httpx.Client(
                transport=self._transport, timeout=self._timeout_s
            ) as client:
                response = client.post(self._url, json=payload)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMTimeout(
                f"ollama request timed out after {self._timeout_s}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMConnectionError(f"ollama request failed: {exc}") from exc

        return _parse_response(response)


def _parse_response(response: httpx.Response) -> LLMResponse:
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMConnectionError(
            f"ollama response was not valid JSON: {response.text[:200]!r}"
        ) from exc

    missing = [field for field in _REQUIRED_RESPONSE_FIELDS if field not in data]
    if missing:
        raise LLMConnectionError(
            f"ollama response missing expected field(s) {missing}: {data!r}"
        )

    return LLMResponse(
        text=data["response"],
        tokens_in=data["prompt_eval_count"],
        tokens_out=data["eval_count"],
    )
