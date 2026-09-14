"""Schema tests: mostly exercising CHECK/FK constraints, since those encode
domain rules (quarantine needs a reason, `done` needs a doc_type, files can't
point at a nonexistent document) that later stages must not be able to violate
by accident.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from extractor.db import connect, init_schema

NOW = "2026-01-01T00:00:00Z"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "test.sqlite")


def insert_run(conn: sqlite3.Connection, **overrides: object) -> int:
    fields = {
        "started_at": NOW,
        "workers": 4,
        "backend": "fake",
        "model": "fake-model",
        "config_path": "config/default.toml",
        "input_path": "data/corpus",
        **overrides,
    }
    columns = ", ".join(fields)
    placeholders = ", ".join(f":{name}" for name in fields)
    cur = conn.execute(f"INSERT INTO runs ({columns}) VALUES ({placeholders})", fields)
    return cur.lastrowid


def insert_document(conn: sqlite3.Connection, doc_id: str, **overrides: object) -> None:
    fields = {
        "id": doc_id,
        "identity_kind": "text",
        "status": "pending",
        "quarantine_reason": None,
        "doc_type": None,
        "summary": "",
        "created_at": NOW,
        "updated_at": NOW,
        **overrides,
    }
    conn.execute(
        "INSERT INTO documents "
        "(id, identity_kind, status, quarantine_reason, doc_type, summary, created_at, updated_at) "
        "VALUES (:id, :identity_kind, :status, :quarantine_reason, :doc_type, :summary, "
        ":created_at, :updated_at)",
        fields,
    )


def test_schema_creates_all_tables(conn: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == {"runs", "documents", "files", "token_ledger"}


def test_wal_mode_enabled(conn: sqlite3.Connection) -> None:
    (mode,) = conn.execute("PRAGMA journal_mode;").fetchone()
    assert mode == "wal"


def test_foreign_keys_enabled(conn: sqlite3.Connection) -> None:
    (enabled,) = conn.execute("PRAGMA foreign_keys;").fetchone()
    assert enabled == 1


def test_init_schema_is_idempotent(conn: sqlite3.Connection) -> None:
    init_schema(conn)
    init_schema(conn)


def test_happy_path_insert_across_all_tables(conn: sqlite3.Connection) -> None:
    run_id = insert_run(conn)
    insert_document(conn, "doc1")
    conn.execute(
        "INSERT INTO files (path, format, byte_sha256, size_bytes, document_id, discovered_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Faktury/f1.pdf", "pdf", "a" * 64, 1234, "doc1", NOW),
    )
    conn.execute(
        "INSERT INTO token_ledger "
        "(run_id, document_id, attempt, reserved_at, reserved_tokens) VALUES (?, ?, ?, ?, ?)",
        (run_id, "doc1", 1, NOW, 1500),
    )


def test_document_id_must_be_unique(conn: sqlite3.Connection) -> None:
    insert_document(conn, "doc1")
    with pytest.raises(sqlite3.IntegrityError):
        insert_document(conn, "doc1")


def test_invalid_status_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_document(conn, "doc1", status="finished")


def test_quarantined_requires_a_reason(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_document(conn, "doc1", status="quarantined", quarantine_reason=None)


def test_quarantine_reason_must_be_closed_enum(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_document(conn, "doc1", status="quarantined", quarantine_reason="oops")


def test_non_quarantined_forbids_a_reason(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_document(
            conn, "doc1", status="pending", quarantine_reason="corrupt_file"
        )


def test_quarantined_with_reason_succeeds(conn: sqlite3.Connection) -> None:
    insert_document(
        conn, "doc1", status="quarantined", quarantine_reason="corrupt_file"
    )


def test_done_requires_doc_type(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_document(conn, "doc1", status="done", doc_type=None)


def test_done_with_doc_type_succeeds(conn: sqlite3.Connection) -> None:
    insert_document(conn, "doc1", status="done", doc_type="invoice")


def test_invalid_doc_type_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_document(conn, "doc1", status="done", doc_type="not_a_real_type")


def test_file_rejects_unknown_document_id(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO files (path, format, byte_sha256, size_bytes, document_id, discovered_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Faktury/f1.pdf", "pdf", "a" * 64, 1234, "no-such-doc", NOW),
        )


def test_file_rejects_unknown_format(conn: sqlite3.Connection) -> None:
    insert_document(conn, "doc1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO files (path, format, byte_sha256, size_bytes, document_id, discovered_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Faktury/f1.xyz", "xyz", "a" * 64, 1234, "doc1", NOW),
        )


def test_file_content_source_defaults_to_own(conn: sqlite3.Connection) -> None:
    insert_document(conn, "doc1")
    conn.execute(
        "INSERT INTO files (path, format, byte_sha256, size_bytes, document_id, discovered_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Faktury/f1.pdf", "pdf", "a" * 64, 1234, "doc1", NOW),
    )
    (content_source,) = conn.execute(
        "SELECT content_source FROM files WHERE path = 'Faktury/f1.pdf'"
    ).fetchone()
    assert content_source == "own"


def test_file_content_source_records_eml_attachment(conn: sqlite3.Connection) -> None:
    insert_document(conn, "doc1")
    conn.execute(
        "INSERT INTO files "
        "(path, format, byte_sha256, size_bytes, document_id, content_source, discovered_at) "
        "VALUES (?, 'eml', ?, 100, 'doc1', 'eml_attachment', ?)",
        ("Faktury/f1_mailem.eml", "a" * 64, NOW),
    )
    (content_source,) = conn.execute(
        "SELECT content_source FROM files WHERE path = 'Faktury/f1_mailem.eml'"
    ).fetchone()
    assert content_source == "eml_attachment"


def test_file_rejects_invalid_content_source(conn: sqlite3.Connection) -> None:
    insert_document(conn, "doc1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO files "
            "(path, format, byte_sha256, size_bytes, document_id, content_source, discovered_at) "
            "VALUES (?, 'eml', ?, 100, 'doc1', 'made_up', ?)",
            ("Faktury/f1.eml", "a" * 64, NOW),
        )


def test_two_files_can_share_one_document_id(conn: sqlite3.Connection) -> None:
    insert_document(conn, "doc1")
    conn.execute(
        "INSERT INTO files (path, format, byte_sha256, size_bytes, document_id, discovered_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Faktury/f1.pdf", "pdf", "a" * 64, 1234, "doc1", NOW),
    )
    conn.execute(
        "INSERT INTO files (path, format, byte_sha256, size_bytes, document_id, discovered_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Faktury/f1_copy.pdf", "pdf", "a" * 64, 1234, "doc1", NOW),
    )
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM files WHERE document_id = 'doc1'"
    ).fetchone()
    assert count == 2


def test_token_ledger_rejects_unknown_run_id(conn: sqlite3.Connection) -> None:
    insert_document(conn, "doc1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO token_ledger "
            "(run_id, document_id, attempt, reserved_at, reserved_tokens) VALUES (?, ?, ?, ?, ?)",
            (999, "doc1", 1, NOW, 1500),
        )


def test_token_ledger_rejects_duplicate_attempt(conn: sqlite3.Connection) -> None:
    run_id = insert_run(conn)
    insert_document(conn, "doc1")
    conn.execute(
        "INSERT INTO token_ledger "
        "(run_id, document_id, attempt, reserved_at, reserved_tokens) VALUES (?, ?, ?, ?, ?)",
        (run_id, "doc1", 1, NOW, 1500),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO token_ledger "
            "(run_id, document_id, attempt, reserved_at, reserved_tokens) VALUES (?, ?, ?, ?, ?)",
            (run_id, "doc1", 1, NOW, 200),
        )


def test_invalid_stop_reason_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert_run(conn, stop_reason="gave_up")


def test_report_balance_falls_out_of_files_and_documents(
    conn: sqlite3.Connection,
) -> None:
    """input_files = duplicate_files + unique_documents, computed by query
    rather than stored — this pins down that the arithmetic actually works.
    """
    insert_document(conn, "doc1", status="done", doc_type="invoice")
    insert_document(
        conn, "doc2", status="quarantined", quarantine_reason="corrupt_file"
    )
    for path in ("a.pdf", "a_copy.pdf", "a_copy2.pdf"):
        conn.execute(
            "INSERT INTO files (path, format, byte_sha256, size_bytes, document_id, discovered_at) "
            "VALUES (?, 'pdf', ?, 10, 'doc1', ?)",
            (path, "a" * 64, NOW),
        )
    conn.execute(
        "INSERT INTO files (path, format, byte_sha256, size_bytes, document_id, discovered_at) "
        "VALUES ('b.pdf', 'pdf', ?, 10, 'doc2', ?)",
        ("b" * 64, NOW),
    )

    (input_files,) = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    (unique_documents,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    duplicate_files = input_files - unique_documents

    assert input_files == 4
    assert unique_documents == 2
    assert duplicate_files == 2
