"""Stage 2 end-to-end: walking, hashing, dedup, and the files/documents
writes, against both the real corpus and small synthetic fixtures for the
guards (zip-slip-shaped names, zip bombs) that aren't exercised by it.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from extractor.db import connect
from extractor.inventory import build_inventory

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "data" / "corpus"
EXPECTED_JSONL = REPO_ROOT / "data" / "expected.jsonl"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "test.sqlite")


def _expected_groups() -> list[list[str]]:
    with EXPECTED_JSONL.open(encoding="utf-8") as fh:
        return sorted(sorted(json.loads(line)["files"]) for line in fh)


def _actual_groups(conn: sqlite3.Connection) -> list[list[str]]:
    rows = conn.execute("SELECT path, document_id FROM files").fetchall()
    by_doc: dict[str, list[str]] = {}
    for path, doc_id in rows:
        by_doc.setdefault(doc_id, []).append(path)
    return sorted(sorted(paths) for paths in by_doc.values())


def test_full_corpus_matches_expected_jsonl_grouping(conn: sqlite3.Connection) -> None:
    """The real integration test: everything decided in inventory.py, run
    against the actual synthetic corpus, must dedup exactly the way
    data/expected.jsonl says a clean run would — including the 300MB fixture.
    """
    stats = build_inventory(conn, CORPUS)
    assert _actual_groups(conn) == _expected_groups()
    assert stats.input_files == 48
    assert stats.unique_documents == 38
    assert stats.quarantined == 8


def test_quarantined_documents_have_all_eight_corrupt_reasons_accounted_for(
    conn: sqlite3.Connection,
) -> None:
    build_inventory(conn, CORPUS)
    rows = conn.execute(
        "SELECT quarantine_reason, COUNT(*) FROM documents "
        "WHERE status = 'quarantined' GROUP BY quarantine_reason"
    ).fetchall()
    by_reason = dict(rows)
    assert by_reason == {
        "unsupported_format": 3,
        "corrupt_file": 3,
        "no_text_layer": 1,
        "empty_text": 1,
    }


def test_report_balance_input_files_equals_duplicates_plus_unique(
    conn: sqlite3.Connection,
) -> None:
    stats = build_inventory(conn, CORPUS)
    (input_files,) = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    (unique_documents,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    assert stats.input_files == input_files
    assert stats.unique_documents == unique_documents
    duplicate_files = input_files - unique_documents
    assert duplicate_files == 10


def test_eml_with_real_attachment_records_content_source(
    conn: sqlite3.Connection,
) -> None:
    build_inventory(conn, CORPUS)
    (content_source,) = conn.execute(
        "SELECT content_source FROM files WHERE path = "
        "'Faktury/Kopie zapasowe/Faktura_FV_2024_03_018_mailem.eml'"
    ).fetchone()
    assert content_source == "eml_attachment"

    (content_source,) = conn.execute(
        "SELECT content_source FROM files WHERE path = "
        "'Faktury/2024/Dostawcy zewnętrzni/Faktura_FV_2024_03_018.pdf'"
    ).fetchone()
    assert content_source == "own"


def test_build_inventory_is_idempotent(conn: sqlite3.Connection) -> None:
    build_inventory(conn, CORPUS)
    groups_first = _actual_groups(conn)
    stats_second = build_inventory(conn, CORPUS)
    groups_second = _actual_groups(conn)

    assert groups_first == groups_second
    assert stats_second.input_files == 48
    assert stats_second.unique_documents == 38


def test_duplicate_bytes_in_directory_dedup_to_one_document(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.txt").write_text("Faktura testowa, kwota 100 zł", encoding="utf-8")
    (src / "b.txt").write_text("Faktura testowa, kwota 100 zł", encoding="utf-8")

    build_inventory(conn, src)
    (unique_documents,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    (input_files,) = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    assert unique_documents == 1
    assert input_files == 2


def test_directory_paths_are_relative_posix_and_nfc(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    src = tmp_path / "in"
    (src / "Faktury").mkdir(parents=True)
    (src / "Faktury" / "f.txt").write_text("tresc dokumentu", encoding="utf-8")

    build_inventory(conn, src)
    (path,) = conn.execute("SELECT path FROM files").fetchone()
    assert path == "Faktury/f.txt"


# --- zip input -----------------------------------------------------------


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def test_zip_input_is_walked_and_deduped(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    archive = tmp_path / "archive.zip"
    _write_zip(
        archive,
        {
            "a.txt": b"Faktura testowa, kwota 100 zl",
            "copy/a.txt": b"Faktura testowa, kwota 100 zl",
            "b.txt": b"Inny dokument, zupelnie inna tresc",
        },
    )
    stats = build_inventory(conn, archive)
    assert stats.input_files == 3
    assert stats.unique_documents == 2


def test_zip_entry_with_path_traversal_name_is_processed_safely(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A zip-slip-shaped entry name must never be used to build a
    filesystem path — entries are only ever read through the zip handle
    (extractor/inventory.py's module docstring). This confirms that holds:
    no traversal, no crash, and the file still gets a row (not silently
    dropped — docs/ZADANIE.md requirement 7).
    """
    archive = tmp_path / "archive.zip"
    _write_zip(archive, {"../../../../tmp/evil.txt": b"payload content"})

    stats = build_inventory(conn, archive)
    assert stats.input_files == 1
    assert not Path("/tmp/evil.txt").exists()

    (path,) = conn.execute("SELECT path FROM files").fetchone()
    assert path == "../../../../tmp/evil.txt"


def test_oversized_zip_entry_is_quarantined_without_reading(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import extractor.inventory as inventory_module

    monkeypatch.setattr(inventory_module, "MAX_ENTRY_BYTES", 10)

    archive = tmp_path / "archive.zip"
    _write_zip(archive, {"huge.txt": b"this content is longer than ten bytes"})

    stats = build_inventory(conn, archive)
    assert stats.input_files == 1
    assert stats.quarantined == 1
    (reason, size_bytes) = conn.execute(
        "SELECT d.quarantine_reason, f.size_bytes FROM documents d "
        "JOIN files f ON f.document_id = d.id"
    ).fetchone()
    assert reason == "corrupt_file"
    assert size_bytes == len(b"this content is longer than ten bytes")


def test_directory_and_zip_of_same_content_agree_on_identity(
    tmp_path: Path,
) -> None:
    """Same bytes via two different input mechanisms must land on the same
    document id — the identity only depends on content, not how it arrived.
    """
    content = "Zawiadomienie o zmianie osoby kontaktowej."

    dir_input = tmp_path / "dir_in"
    dir_input.mkdir()
    (dir_input / "note.txt").write_text(content, encoding="utf-8")
    conn_dir = connect(tmp_path / "dir.sqlite")
    build_inventory(conn_dir, dir_input)
    (dir_id,) = conn_dir.execute("SELECT document_id FROM files").fetchone()

    zip_input = tmp_path / "zip_in.zip"
    _write_zip(zip_input, {"note.txt": content.encode("utf-8")})
    conn_zip = connect(tmp_path / "zip.sqlite")
    build_inventory(conn_zip, zip_input)
    (zip_id,) = conn_zip.execute("SELECT document_id FROM files").fetchone()

    assert dir_id == zip_id


def test_invalid_input_path_raises(tmp_path: Path, conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="directory or a zip archive"):
        build_inventory(conn, tmp_path / "does-not-exist")
