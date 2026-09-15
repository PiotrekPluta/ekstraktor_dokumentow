from __future__ import annotations

import pytest
from pydantic import ValidationError

from extractor.schema import DocType, ExtractedFields

VALID_PAYLOAD = {
    "doc_type": "invoice",
    "counterparty_name": "Test Sp. z o.o.",
    "counterparty_tax_id": "5260001246",
    "issue_date": "2024-03-12",
    "due_date": "2024-03-26",
    "gross_amount": "861.00",
    "currency": "PLN",
    "summary": "Faktura VAT za usługi doradcze.",
}


def test_accepts_a_full_valid_payload() -> None:
    fields = ExtractedFields.model_validate(VALID_PAYLOAD)
    assert fields.doc_type is DocType.INVOICE
    assert fields.counterparty_tax_id == "5260001246"


def test_accepts_all_fields_null_except_doc_type_and_summary() -> None:
    payload = {
        **VALID_PAYLOAD,
        "counterparty_name": None,
        "counterparty_tax_id": None,
        "issue_date": None,
        "due_date": None,
        "gross_amount": None,
        "currency": None,
    }
    fields = ExtractedFields.model_validate(payload)
    assert fields.gross_amount is None
    assert fields.currency is None


def test_rejects_doc_type_outside_the_closed_enum() -> None:
    with pytest.raises(ValidationError):
        ExtractedFields.model_validate({**VALID_PAYLOAD, "doc_type": "invitation"})


def test_json_schema_includes_all_eight_field_names() -> None:
    schema = ExtractedFields.model_json_schema()
    properties = schema["properties"]
    for name in (
        "doc_type",
        "counterparty_name",
        "counterparty_tax_id",
        "issue_date",
        "due_date",
        "gross_amount",
        "currency",
        "summary",
    ):
        assert name in properties
