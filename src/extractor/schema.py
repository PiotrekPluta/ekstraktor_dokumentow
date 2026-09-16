"""The eight extracted fields, as a Pydantic model (docs/PROJECT_NOTES.md
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
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


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
    gross_amount: Decimal | None = None
    currency: str | None = None
    summary: str
