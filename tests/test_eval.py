"""Stage 8 acceptance tests for `eval` (docs/ZADANIE.md §3, docs/DATA_SPEC.md
§8). Documents/files seeded directly against a real db (test_orchestrate.py's
pattern) so each test controls exactly what "the extractor produced" without
needing a real model — `eval`'s job starts from an already-populated db.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from extractor.db import connect
from extractor.eval import evaluate

_NOW = "2024-01-01T00:00:00Z"


def _seed(conn: sqlite3.Connection, doc_id: str, path: str, **fields: object) -> None:
    # A document quarantined at inventory time (unrecognised/corrupt, per
    # docs/PROJECT_NOTES.md §8 Stage 2/3) has doc_type NULL with status
    # 'quarantined', not 'done' — the schema's own CHECK forbids
    # 'done'-with-null-doc_type, matching docs/ZADANIE.md §3's rule that
    # such files get `doc_type: null` in `expected.jsonl` too.
    status = "done" if fields.get("doc_type") is not None else "quarantined"
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO documents "
        "(id, identity_kind, status, quarantine_reason, doc_type, "
        "counterparty_name, counterparty_tax_id, issue_date, due_date, "
        "gross_amount, currency, summary, created_at, updated_at) "
        "VALUES (?, 'text', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            doc_id,
            status,
            "unsupported_format" if status == "quarantined" else None,
            fields.get("doc_type"),
            fields.get("counterparty_name"),
            fields.get("counterparty_tax_id"),
            fields.get("issue_date"),
            fields.get("due_date"),
            fields.get("gross_amount"),
            fields.get("currency"),
            fields.get("summary", ""),
            _NOW,
            _NOW,
        ),
    )
    conn.execute(
        "INSERT INTO files "
        "(path, format, byte_sha256, size_bytes, document_id, discovered_at) "
        "VALUES (?, 'txt', ?, 1, ?, ?)",
        (path, f"hash-{doc_id}", doc_id, _NOW),
    )
    conn.execute("COMMIT")


_EXPECTED_INVOICE = {
    "doc_type": "invoice",
    "counterparty_name": 'Zakład Usługowy "Merkury" Sp. z o.o.',
    "counterparty_tax_id": "8104482459",
    "issue_date": "2024-03-12",
    "due_date": "2024-04-14",
    "gross_amount": "1845.00",
    "currency": "PLN",
    "summary": "Faktura za przegląd techniczny windy.",
    "files": ["invoice.pdf"],
}


def test_perfect_match_scores_100_percent_on_every_field(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    _seed(
        conn,
        "doc1",
        "invoice.pdf",
        doc_type="invoice",
        counterparty_name='Zakład Usługowy "Merkury" Sp. z o.o.',
        counterparty_tax_id="8104482459",
        issue_date="2024-03-12",
        due_date="2024-04-14",
        gross_amount="1845.00",
        currency="PLN",
        summary="Faktura za przegląd windy wystawiona przez Merkury.",
    )

    result = evaluate(conn, [_EXPECTED_INVOICE])

    for score in result.field_scores:
        assert score.correct == 1, score.field
        assert score.accuracy == 1.0
    assert result.not_found == 0
    assert result.dedup_mismatches == 0


def test_name_and_tax_id_match_despite_formatting_differences(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    _seed(
        conn,
        "doc1",
        "invoice.pdf",
        doc_type="invoice",
        counterparty_name='  zakład usługowy "merkury" sp. z o.o.  ',
        counterparty_tax_id="810-448-24-59",
        issue_date="2024-03-12",
        due_date="2024-04-14",
        gross_amount="1845.0",  # one decimal, same value
        currency="pln",
        summary="Faktura za przegląd windy wystawiona przez Merkury.",
    )

    result = evaluate(conn, [_EXPECTED_INVOICE])

    by_field = {s.field: s for s in result.field_scores}
    assert by_field["counterparty_name"].correct == 1
    assert by_field["counterparty_tax_id"].correct == 1
    assert by_field["gross_amount"].correct == 1
    assert by_field["currency"].correct == 1


def test_wrong_values_and_nulls_are_scored_as_mismatches(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    _seed(conn, "doc1", "invoice.pdf", doc_type="contract")  # everything else null

    result = evaluate(conn, [_EXPECTED_INVOICE])

    by_field = {s.field: s for s in result.field_scores}
    assert by_field["doc_type"].correct == 0
    assert by_field["counterparty_name"].correct == 0
    assert by_field["gross_amount"].correct == 0
    assert by_field["counterparty_name"].total == 1


def test_null_matches_null_for_a_quarantined_or_unrecognised_document(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "db.sqlite")
    _seed(conn, "doc1", "corrupt.txt")  # every field NULL, doc_type NULL too

    expected = {
        "doc_type": None,
        "counterparty_name": None,
        "counterparty_tax_id": None,
        "issue_date": None,
        "due_date": None,
        "gross_amount": None,
        "currency": None,
        "summary": "",
        "files": ["corrupt.txt"],
    }

    result = evaluate(conn, [expected])

    for score in result.field_scores:
        assert score.correct == 1, score.field
    # summary isn't scored at all for a null-doc_type expected record.
    assert result.summary_scored == 0
    assert result.summary_loose_score == 1.0


def test_document_missing_from_the_db_counts_as_wrong_not_excluded(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "db.sqlite")  # nothing seeded at all

    result = evaluate(conn, [_EXPECTED_INVOICE])

    assert result.not_found == 1
    for score in result.field_scores:
        if score.field == "doc_type":
            assert score.correct == 0
        assert score.total == 1


def test_dedup_mismatch_is_flagged_but_still_scored(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    # Ground truth says these two files are one document; this db disagrees.
    _seed(conn, "doc_a", "invoice.pdf", doc_type="invoice", currency="PLN")
    _seed(conn, "doc_b", "invoice_copy.pdf", doc_type="invoice", currency="USD")
    expected = dict(_EXPECTED_INVOICE, files=["invoice.pdf", "invoice_copy.pdf"])

    result = evaluate(conn, [expected])

    assert result.dedup_mismatches == 1
    assert result.not_found == 0
    by_field = {s.field: s for s in result.field_scores}
    assert by_field["currency"].total == 1  # still scored once, not dropped


def test_summary_loose_metric_checks_shape_and_keywords(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    _seed(
        conn,
        "good",
        "good.pdf",
        doc_type="invoice",
        counterparty_name="Merkury",
        summary="Faktura wystawiona przez Merkury za usługi.",
    )
    _seed(
        conn,
        "two_sentences",
        "two.pdf",
        doc_type="invoice",
        counterparty_name="Merkury",
        summary="Faktura od Merkury. Termin płatności minął.",
    )
    _seed(
        conn,
        "missing_keyword",
        "missing.pdf",
        doc_type="invoice",
        counterparty_name="Merkury",
        summary="Dokument wystawiony przez sprzedawcę.",
    )

    def _expected_for(path: str) -> dict:
        return dict(
            _EXPECTED_INVOICE,
            counterparty_name="Merkury",
            files=[path],
        )

    good = evaluate(conn, [_expected_for("good.pdf")])
    two_sentences = evaluate(conn, [_expected_for("two.pdf")])
    missing_keyword = evaluate(conn, [_expected_for("missing.pdf")])

    assert good.summary_loose_score == 1.0
    assert two_sentences.summary_loose_score == 0.0
    assert missing_keyword.summary_loose_score == 0.0
