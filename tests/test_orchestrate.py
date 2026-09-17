"""Stage 7 acceptance tests (docs/plan/Stage_7_plan.md), all against the
`fake` backend (requirement 10): resumability, no repeated model calls for
completed documents, cumulative `--limit`/`--budget`, worker-count
independence, the repair-attempt path, circuit-breaker stop behaviour, and
integrity (a document's content can only ever affect its own row).
`test_workers_1_and_workers_16_produce_identical_final_documents` is a
Stage 9 addition — requirement 5's stability bar is literally "N=1 i
N=16", which the original Stage 7 test (1 vs 4) didn't hit. The real,
process-level `SIGKILL` acceptance test lives separately in
`test_resume_subprocess.py`; the resumability tests here exercise the
in-process orchestration *logic* only.

Documents are seeded directly into `documents` rows (bypassing
`inventory.build_inventory`) so each test controls exactly which rows are
`pending`/`in_progress`/`done` going in — orchestration doesn't care how a
row got there, only its `status` and `source_text`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from extractor.config import Config
from extractor.db import connect
from extractor.llm.client import LLMResponse
from extractor.llm.fake import FakeLLMClient
from extractor.llm.resilience import ResilientLLMClient
from extractor.orchestrate import run as orchestrate_run

_NOW = "2024-01-01T00:00:00Z"

# A source_text template every field-check in validate_extraction can find
# a match for: counterparty name/NIP literally present, date-shaped and
# amount-shaped text present (issue_date/due_date/gross_amount only require
# *some* candidate to exist, not an exact match — see validation.py).
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


def _seed_document(
    conn: sqlite3.Connection,
    doc_id: str,
    *,
    status: str = "pending",
    source_text: str | None = SOURCE_TEMPLATE,
    doc_type: str | None = None,
    quarantine_reason: str | None = None,
    summary: str = "",
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO documents "
        "(id, identity_kind, status, quarantine_reason, doc_type, summary, "
        "source_text, created_at, updated_at) "
        "VALUES (?, 'text', ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, status, quarantine_reason, doc_type, summary, source_text, _NOW, _NOW),
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


def _row(conn: sqlite3.Connection, doc_id: str) -> tuple:
    return conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()


# --- full run / basic completion --------------------------------------------


def test_full_run_completes_all_pending_documents(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    _seed_document(conn, "doc1")
    _seed_document(conn, "doc2")
    _seed_document(conn, "doc3")

    fake = FakeLLMClient(
        response=LLMResponse(text=VALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10)
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)

    result = orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(),
        config_path="config/default.toml",
        count_tokens=_fixed_counter(20),
    )

    assert result.stop_reason == "completed"
    statuses = [row["status"] for row in conn.execute("SELECT status FROM documents")]
    assert statuses == ["done", "done", "done"]
    assert len(fake.calls) == 3


# --- resume / no repeated model calls ---------------------------------------


def test_in_progress_document_is_reset_and_completed_not_double_processed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    _seed_document(conn, "doc1", status="in_progress")

    fake = FakeLLMClient(
        response=LLMResponse(text=VALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10)
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)

    result = orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(),
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    assert result.stop_reason == "completed"
    (status,) = conn.execute(
        "SELECT status FROM documents WHERE id = 'doc1'"
    ).fetchone()
    assert status == "done"
    assert len(fake.calls) == 1


def test_already_done_document_gets_no_model_call_and_is_untouched(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    _seed_document(
        conn, "already_done", status="done", doc_type="invoice", summary="seeded"
    )
    _seed_document(conn, "still_pending")
    before = _row(conn, "already_done")

    fake = FakeLLMClient(
        response=LLMResponse(text=VALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10)
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)

    orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(),
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    after = _row(conn, "already_done")
    assert tuple(before) == tuple(after)
    assert len(fake.calls) == 1  # only "still_pending" was ever attempted


# --- --limit is cumulative across invocations -------------------------------


def test_limit_is_cumulative_across_two_run_calls(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    for i in range(5):
        _seed_document(conn, f"doc{i}")

    fake = FakeLLMClient(
        response=LLMResponse(text=VALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10)
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)
    config = _config(limit=3)

    first = orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        config,
        config_path="c",
        count_tokens=_fixed_counter(20),
    )
    assert first.stop_reason == "limit_reached"
    (done_count,) = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status = 'done'"
    ).fetchone()
    assert done_count == 3
    assert len(fake.calls) == 3

    second = orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        config,
        config_path="c",
        count_tokens=_fixed_counter(20),
    )
    assert second.stop_reason == "limit_reached"
    (done_count,) = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status = 'done'"
    ).fetchone()
    assert done_count == 3  # not limit(3) + limit(3) = 6
    assert len(fake.calls) == 3  # no new calls made


# --- --budget stops before calling the model --------------------------------


def test_budget_stops_before_the_model_call_document_stays_pending(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    _seed_document(conn, "doc1")

    fake = FakeLLMClient(
        response=LLMResponse(text=VALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10)
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)
    config = _config(budget=10)  # any reservation (>= max_output_tokens=50) exceeds it

    result = orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        config,
        config_path="c",
        count_tokens=_fixed_counter(5),
    )

    assert result.stop_reason == "budget_exhausted"
    (status,) = conn.execute(
        "SELECT status FROM documents WHERE id = 'doc1'"
    ).fetchone()
    assert status == "pending"
    assert len(fake.calls) == 0


# --- worker count doesn't change the result set -----------------------------


_DOCUMENT_COLUMNS = (
    "id, status, quarantine_reason, doc_type, counterparty_name, "
    "counterparty_tax_id, issue_date, due_date, gross_amount, currency, summary"
)


def _run_with_workers(tmp_path: Path, db_name: str, n_docs: int, workers: int) -> list:
    db_path = tmp_path / db_name
    conn = connect(db_path)
    for i in range(n_docs):
        _seed_document(conn, f"doc{i}")
    conn.close()

    response = LLMResponse(text=VALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10)
    client = ResilientLLMClient(FakeLLMClient(response=response), sleep=lambda _: None)
    orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(workers=workers),
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    conn = connect(db_path)
    rows = conn.execute(
        f"SELECT {_DOCUMENT_COLUMNS} FROM documents ORDER BY id"
    ).fetchall()
    return [tuple(r) for r in rows]


def test_workers_1_and_workers_4_produce_identical_final_documents(
    tmp_path: Path,
) -> None:
    rows_a = _run_with_workers(tmp_path, "a.sqlite", n_docs=8, workers=1)
    rows_b = _run_with_workers(tmp_path, "b.sqlite", n_docs=8, workers=4)
    assert rows_a == rows_b


def test_workers_1_and_workers_16_produce_identical_final_documents(
    tmp_path: Path,
) -> None:
    """docs/ZADANIE.md requirement 5's literal stability bar ("N = 1 i N =
    16 na maszynie oceniającej"), with enough documents (20) that 16
    workers are actually all put to work rather than mostly sitting idle.
    """
    rows_a = _run_with_workers(tmp_path, "a.sqlite", n_docs=20, workers=1)
    rows_b = _run_with_workers(tmp_path, "b.sqlite", n_docs=20, workers=16)
    assert rows_a == rows_b


# --- repair-attempt path -----------------------------------------------------


def test_repair_path_succeeds_on_second_response(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    _seed_document(conn, "doc1")

    fake = FakeLLMClient(
        responses=[
            LLMResponse(text=INVALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10),
            LLMResponse(text=VALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10),
        ]
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)

    result = orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(),
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    assert result.stop_reason == "completed"
    (status,) = conn.execute(
        "SELECT status FROM documents WHERE id = 'doc1'"
    ).fetchone()
    assert status == "done"
    assert len(fake.calls) == 2
    (attempt_count,) = conn.execute(
        "SELECT COUNT(*) FROM token_ledger WHERE document_id = 'doc1'"
    ).fetchone()
    assert attempt_count == 2


def test_repair_exhausted_quarantines_and_run_continues(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    _seed_document(conn, "doc1")
    _seed_document(conn, "doc2")

    fake = FakeLLMClient(
        response=LLMResponse(text=INVALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10)
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)

    result = orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(),
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    assert result.stop_reason == "completed"  # not aborted
    rows = conn.execute(
        "SELECT id, status, quarantine_reason FROM documents"
    ).fetchall()
    for row in rows:
        assert row["status"] == "quarantined"
        assert row["quarantine_reason"] == "llm_invalid_output"
    assert len(fake.calls) == 4  # 2 documents x (initial + one repair attempt)


# --- circuit breaker stop behaviour ------------------------------------------


def test_backend_unavailable_stops_run_and_loses_no_document(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    for i in range(5):
        _seed_document(conn, f"doc{i}")

    fake = FakeLLMClient(always_fail=True)
    # max_retries >= failure_threshold: the very first document's own
    # complete() call retries enough times internally to trip the breaker
    # itself (LLMBackendUnavailable), rather than exhausting its own retry
    # budget first and getting quarantined as a document-scoped failure
    # (decision 6) — the scenario this test wants is a whole-run outage
    # caught before any single document's attempt is individually blamed.
    client = ResilientLLMClient(
        fake, max_retries=3, failure_threshold=2, sleep=lambda _: None
    )

    result = orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(workers=1),
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    assert result.stop_reason == "backend_unavailable"
    statuses = {row["status"] for row in conn.execute("SELECT status FROM documents")}
    assert statuses == {"pending"}  # nothing done, nothing quarantined, nothing lost
    (total,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert total == 5


def test_workers_8_against_down_backend_completes_without_crash(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    for i in range(20):
        _seed_document(conn, f"doc{i}")

    fake = FakeLLMClient(always_fail=True)
    client = ResilientLLMClient(
        fake, max_retries=0, failure_threshold=3, sleep=lambda _: None
    )

    result = orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(workers=8),
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    assert result.stop_reason == "backend_unavailable"
    assert client.circuit_breaker_open
    # No row left claimed-but-abandoned.
    statuses = {row["status"] for row in conn.execute("SELECT status FROM documents")}
    assert "in_progress" not in statuses


# --- integrity: a document's content only ever touches its own row ---------


def test_injection_shaped_content_only_changes_its_own_row(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    _seed_document(
        conn,
        "victim",
        status="done",
        doc_type="invoice",
        summary="untouched victim row",
    )
    _seed_document(conn, "attacker")
    victim_before = _row(conn, "victim")

    injected_summary = (
        "'; UPDATE documents SET status='quarantined' WHERE id='victim'; --"
    )
    response_text = json.dumps(
        {
            "doc_type": "invoice",
            "counterparty_name": "Acme Sp. z o.o.",
            "counterparty_tax_id": "1234567890",
            "issue_date": "2024-01-01",
            "due_date": "2024-01-15",
            "gross_amount": "100.00",
            "currency": "PLN",
            "summary": injected_summary,
        }
    )

    fake = FakeLLMClient(
        response=LLMResponse(text=response_text, tokens_in=10, tokens_out=10)
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)

    orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(),
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    victim_after = _row(conn, "victim")
    assert tuple(victim_before) == tuple(victim_after)

    attacker_after = _row(conn, "attacker")
    assert attacker_after["status"] == "done"
    assert attacker_after["summary"] == injected_summary  # stored as inert data


# --- report-balance precondition --------------------------------------------


def test_report_balance_precondition_holds_after_mixed_run(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    _seed_document(conn, "doc1")  # will succeed
    _seed_document(conn, "doc2")  # will exhaust repair -> quarantined
    _seed_document(conn, "doc3")  # excluded by --limit -> stays pending

    fake = FakeLLMClient(
        responses=[
            LLMResponse(text=VALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10),
            LLMResponse(text=INVALID_RESPONSE_TEXT, tokens_in=10, tokens_out=10),
        ]
    )
    client = ResilientLLMClient(fake, sleep=lambda _: None)

    orchestrate_run(
        db_path,
        tmp_path / "input",
        client,
        _config(workers=1, limit=2),
        config_path="c",
        count_tokens=_fixed_counter(20),
    )

    (unique_documents,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    (processed_ok,) = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status = 'done'"
    ).fetchone()
    (quarantined,) = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status = 'quarantined'"
    ).fetchone()
    (not_started,) = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status = 'pending'"
    ).fetchone()

    assert unique_documents == 3
    assert processed_ok == 1
    assert quarantined == 1
    assert not_started == 1
    assert unique_documents == processed_ok + quarantined + not_started
