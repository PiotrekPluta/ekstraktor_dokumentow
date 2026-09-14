"""Candidate finders are anchors for windowing, not validators — tests
check they find the format variety docs/DATA_SPEC.md actually generates
(§3.1-3.3), including that shape-alike decoys (REGON/KRS look like a NIP
on purpose) are found too, since excluding them isn't this module's job.
"""

from __future__ import annotations

import pytest

from extractor.candidates import (
    find_amount_candidates,
    find_date_candidates,
    find_nip_candidates,
)


@pytest.mark.parametrize(
    "text",
    [
        "1234567890",
        "123-456-78-90",
        "123 456 78 90",
        "PL1234567890",
        "NIP: PL 123-45-67-890",
        "NIP 526-000-12-46",
    ],
)
def test_nip_shaped_formats_found(text: str) -> None:
    assert find_nip_candidates(text)


def test_krs_decoy_is_not_excluded() -> None:
    # docs/DATA_SPEC.md §3.1: KRS is deliberately the same length as a NIP.
    # Distinguishing them (checksum, context) is Stage 6's job, not this
    # one's — a shape-alike decoy is expected to match here.
    assert find_nip_candidates("KRS: 0000123456")


def test_regon_wrong_digit_count_not_matched() -> None:
    assert find_nip_candidates("REGON: 123456785") == []  # 9 digits, not 10


@pytest.mark.parametrize(
    "text",
    [
        "2024-03-12",
        "12.03.2024",
        "12 marca 2024",
        "12 III 2024",
        "March 12, 2024",
        "12 March 2024",
        "03/12/2024",
        "12 stycznia 2024",
        "1 grudnia 2024",
    ],
)
def test_date_formats_found(text: str) -> None:
    assert find_date_candidates(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 234,56 zł", "1 234,56 zł"),
        ("1.234,56 PLN", "1.234,56 PLN"),
        ("1,234.56 EUR", "1,234.56 EUR"),
        ("PLN 1234.56", "PLN 1234.56"),
        ("861,00 zł", "861,00 zł"),
    ],
)
def test_amount_formats_found(text: str, expected: str) -> None:
    matches = find_amount_candidates(text)
    assert matches
    assert matches[0].group() == expected


def test_negative_amount_keeps_its_sign() -> None:
    # docs/DATA_SPEC.md §3.2: correction invoices carry a negative gross
    # amount — losing the sign here would make windowing anchor on the
    # wrong (unsigned) span, and would be outright wrong for Stage 6 later.
    matches = find_amount_candidates("Kwota do zwrotu: -450,00 PLN")
    assert matches
    assert matches[0].group().strip() == "-450,00 PLN"


def test_ungrouped_four_digit_amount_not_truncated() -> None:
    # A plain 1234.56 (no thousands separator) must match in full, not
    # just a trailing "234.56" — \d{1,3} alone would stop after 3 digits.
    matches = find_amount_candidates("PLN 1234.56")
    assert matches
    assert matches[0].group() == "PLN 1234.56"


def test_start_end_bounds_restrict_the_search() -> None:
    text = "NIP: 526-000-12-46 " + ("x" * 100) + " NIP: 111-222-33-44"
    all_matches = find_nip_candidates(text)
    assert len(all_matches) == 2

    bounded = find_nip_candidates(text, start=0, end=30)
    assert len(bounded) == 1
