"""Stage 8 acceptance tests for `report` (docs/ZADANIE.md requirements 6/7).

Follows tests/test_orchestrate.py's pattern: documents/files seeded or
produced directly against a real db, `fake` backend throughout (requirement
10). `test_report_balance_precondition_holds_after_mixed_run` in
test_orchestrate.py already exercised the raw counts by hand at Stage 7;
these tests exercise `compute_report` itself, including the pieces that
test predates (`quarantine_by_reason`, `input_files`/`duplicate_files`,
tokens, cost, wall time, `stop_reason`).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from extractor.config import Config
from extractor.db import connect
from extractor.llm.client import LLMResponse
from extractor.llm.fake import FakeLLMClient
from extractor.llm.resilience import ResilientLLMClient
from extractor.orchestrate import run as orchestrate_run
from extractor.report import compute_report

_NOW = "2024-01-01T00:00:00Z"

SOURCE_TEMPLATE = (
    "Sprzedawca: Acme Sp. z o.o., NIP 1234567890, ul. Testowa 1, "
    "00-001 Warszawa. Data wystawienia: 2024-01-01. "
    "Termin płatności: 2024-01-15. Kwota brutto: 100.00 PLN."
)

VALID_RESPONSE_TEXT = (
    '{"doc_type": "invoice", "counterparty_name": "Acme Sp. z o.o.", '
    '"counterparty_tax_id": "1234567890", "issue_date": "2024-01-01", '
    '"due_date": "2024-01-15", "gross_amount": "100.00", "currency": "PLN", '
    '"summary": "Faktura za usługi."}'
)

INVALID_RESPONSE_TEXT = '{"doc_type": "not-a-real-type", "summary": "x"}'


def _seed_document(conn: sqlite3.Connection, doc_id: str, **kwargs: object) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO documents "
        "(id, identity_kind, status, quarantine_reason, doc_type, summary, "
        "source_text, created_at, updated_at) "
        "VALUES (?, 'text', ?, ?, ?, ?, ?, ?, ?)",
        (
            doc_id,
            kwargs.get("status", "pending"),
            kwargs.get("quarantine_reason"),
            kwargs.get("doc_type"),
            kwargs.get("summary", ""),
            kwargs.get("source_text", SOURCE_TEMPLATE),
            _NOW,
            _NOW,
        ),
    )
    conn.execute("COMMIT")


def _seed_file(
    conn: sqlite3.Connection, path: str, document_id: str, byte_sha256: str
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO files "
        "(path, format, byte_sha256, size_bytes, document_id, discovered_at) "
        "VALUES (?, 'txt', ?, 10, ?, ?)",
        (path, byte_sha256, document_id, _NOW),
    )
    conn.execute("COMMIT")


def _config(**overrides: object) -> Config:
    base: dict[str, object] = {
        "workers": 1,
        "limit": None,
        "budget": None,
        "backend": "fake",
        "raw": {},
        "max_output_tokens": 50,
    }
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


def _fixed_counter(n: int):
    return lambda text: n


def test_empty_db_balances_and_reports_no_stop_reason(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")

    data = compute_report(conn)

    assert data.input_files == 0
    assert data.input_files == data.duplicate_files + data.unique_documents
    assert data.unique_documents == data.processed_ok + data.quarantined + (
        data.not_started
    )
    assert data.tokens_in == 0
    assert data.tokens_out == 0
    assert data.estimated_cost == 0.0
    assert data.stop_reason is None
    assert data.backend is None


def test_balances_with_duplicates_and_mixed_statuses(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    _seed_document(conn, "doc1", status="done", doc_type="invoice")
    _seed_document(conn, "doc2", status="quarantined", quarantine_reason="empty_text")
    _seed_document(conn, "doc3", status="pending")
    _seed_document(conn, "doc4", status="in_progress")
    # doc1 has two files (a duplicate), the rest have one each.
    _seed_file(conn, "a.txt", "doc1", "h1")
    _seed_file(conn, "a_copy.txt", "doc1", "h1")
    _seed_file(conn, "b.txt", "doc2", "h2")
    _seed_file(conn, "c.txt", "doc3", "h3")
    _seed_file(conn, "d.txt", "doc4", "h4")

    data = compute_report(conn)

    assert data.input_files == 5
    assert data.unique_documents == 4
    assert data.duplicate_files == 1
    assert data.input_files == data.duplicate_files + data.unique_documents
    assert data.processed_ok == 1
    assert data.quarantined == 1
    assert data.not_started == 2  # pending + in_progress
    assert data.unique_documents == (
        data.processed_ok + data.quarantined + data.not_started
    )
    assert data.quarantine_by_reason == {"empty_text": 1}


def test_tokens_and_zero_cost_after_a_real_run(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    _seed_document(conn, "doc1")

    fake = FakeLLMClient(
        response=LLMResponse(text=VALID_RESPONSE_TEXT, tokens_in=10, tokens_out=7)
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)
    orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(workers=1),
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    data = compute_report(conn)

    assert data.tokens_in == 10
    assert data.tokens_out == 7
    assert data.estimated_cost == 0.0  # fake backend, no [pricing] in raw config
    assert data.backend == "fake"
    assert data.model == "fake"
    assert data.stop_reason == "completed"
    assert data.wall_time_s >= 0.0


def test_estimated_cost_uses_the_run_s_own_frozen_price(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    _seed_document(conn, "doc1")

    fake = FakeLLMClient(
        response=LLMResponse(text=VALID_RESPONSE_TEXT, tokens_in=1000, tokens_out=500)
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)
    config = _config(
        workers=1,
        raw={
            "pricing": {"fake": {"input_per_million": 2.0, "output_per_million": 4.0}}
        },
    )
    orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        config,
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    data = compute_report(conn)

    # 1000 tok in @ $2/M + 500 tok out @ $4/M = 0.002 + 0.002 = 0.004
    assert data.estimated_cost == 0.004


def test_stop_reason_is_limit_reached_when_a_document_is_left_pending(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    _seed_document(conn, "doc1")
    _seed_document(conn, "doc2")

    fake = FakeLLMClient(
        response=LLMResponse(text=VALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10)
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)
    orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(workers=1, limit=1),
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    data = compute_report(conn)

    assert data.processed_ok == 1
    assert data.not_started == 1
    assert data.stop_reason == "limit_reached"


def test_interrupted_run_is_reported_even_without_a_finish_row(
    tmp_path: Path,
) -> None:
    """No `orchestrate.run` call ever reaches `_finish_run` for this row —
    simulates the db state a SIGKILL leaves behind (ended_at/stop_reason
    both NULL), per this module's docstring on how `report` handles it.
    """
    conn = connect(tmp_path / "db.sqlite")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO runs "
        "(started_at, workers, backend, model, config_path, input_path) "
        "VALUES (?, 1, 'llama_server', 'some-model', 'c', 'in')",
        (_NOW,),
    )
    conn.execute("COMMIT")

    data = compute_report(conn)

    assert data.stop_reason == "interrupted"
    assert data.backend == "llama_server"
    assert data.wall_time_s == 0.0  # no token_ledger rows to estimate from
