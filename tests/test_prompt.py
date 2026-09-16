from __future__ import annotations

from extractor.prompt import build_prompt, build_repair_prompt
from extractor.schema import ExtractedFields


def test_build_prompt_includes_json_schema_fields() -> None:
    prompt = build_prompt("Faktura nr 1, kwota 100 zl")
    for field_name in ExtractedFields.model_fields:
        assert field_name in prompt


def test_build_prompt_includes_seller_not_buyer_instruction() -> None:
    prompt = build_prompt("context")
    assert "kontrahent" in prompt.lower()
    assert "nabywca" in prompt.lower()


def test_build_prompt_includes_currency_precedence_chain() -> None:
    prompt = build_prompt("context")
    lowered = prompt.lower()
    # All six precedence levels from docs/DATA_SPEC.md §3.2 must be present.
    assert "iso" in lowered
    assert "zł" in lowered and "€" in lowered and "£" in lowered
    assert "toronto" in lowered  # the ambiguous-symbol worked example
    assert "vat" in lowered


def test_build_prompt_wraps_context_in_chatml_user_turn() -> None:
    prompt = build_prompt("UNIQUE_CONTEXT_MARKER")
    assert "<|im_start|>system" in prompt
    assert "<|im_start|>user\nUNIQUE_CONTEXT_MARKER<|im_end|>" in prompt
    assert prompt.rstrip().endswith("<|im_start|>assistant")


def test_build_repair_prompt_includes_previous_response_and_error() -> None:
    prompt = build_repair_prompt(
        "context", '{"doc_type": "bogus"}', "doc_type must be one of ..."
    )
    assert '{"doc_type": "bogus"}' in prompt
    assert "doc_type must be one of ..." in prompt


def test_build_repair_prompt_still_includes_schema_and_context() -> None:
    prompt = build_repair_prompt("MY_CONTEXT", "previous", "bad json")
    assert "MY_CONTEXT" in prompt
    for field_name in ExtractedFields.model_fields:
        assert field_name in prompt
