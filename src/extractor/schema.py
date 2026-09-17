r"""The eight extracted fields, as a Pydantic model (docs/PROJECT_NOTES.md
§8 Stage 6).

One schema, two jobs: `model_json_schema()` feeds `LLMRequest.json_schema`
(what's requested of the model), and the model class itself is what
`extractor.validation.validate_extraction()` parses the response into (what
validates it) — one source of truth for the field shape, not two schemas
that could drift apart.

Field types mirror docs/DATA_SPEC.md §8's `expected.jsonl` serialisation
rules directly: `gross_amount` as `Decimal` (renders as a plain "1234.56"
string, matching the corpus's two-decimal-string convention, not a float
that could fail to round-trip); dates as `date` (renders "YYYY-MM-DD");
`summary` is a required string, never `None` — every processed document is
expected to get a one-sentence summary in its own language.

`gross_amount`'s json-schema string-branch pattern is overridden with
`_GROSS_AMOUNT_SCHEMA` instead of Pydantic's own Decimal pattern
(`^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$`) because a live `llama-server` run (this
project's own `LlamaServerClient.complete` posts `model_json_schema()`
straight through as `json_schema`, per `extractor.llm.llama_server`'s
grammar-constrained-decoding note) logs "JSON schema conversion was
incomplete: ... unsupported group syntax, accepting any string" for it —
llama.cpp's schema-to-GBNF converter doesn't support lookahead groups, so
it silently drops the character-set constraint for that anyOf branch
(verified against `vendor/llama-server`: same warning reproduces with the
raw Pydantic schema, disappears with this one). The replacement is
lookahead-free but same semantics (rejects the sign/dot-only degenerate
matches the lookahead was there to exclude — see tests/test_schema.py) and
also avoids `\d`, which this llama.cpp build's grammar converter rejects
too ("unsupported escape") — `[0-9]` only.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, WithJsonSchema

_GROSS_AMOUNT_SCHEMA = {
    "anyOf": [
        {"type": "number"},
        {"type": "string", "pattern": r"^[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)$"},
        {"type": "null"},
    ],
    "default": None,
    "title": "Gross Amount",
}


class DocType(str, Enum):
    INVOICE = "invoice"
    CONTRACT = "contract"
    OFFER = "offer"
    CORRESPONDENCE = "correspondence"
    OTHER = "other"


class ExtractedFields(BaseModel):
    doc_type: DocType
    counterparty_name: str | None = None
    counterparty_tax_id: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    gross_amount: Annotated[Decimal | None, WithJsonSchema(_GROSS_AMOUNT_SCHEMA)] = None
    currency: str | None = None
    summary: str
