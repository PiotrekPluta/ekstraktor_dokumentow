"""Stage 2: inventory + deduplication.

Walks `--input` (a directory or a zip archive), computes each file's
identity, and writes `files`/`documents` rows (extractor/db.py). Two dedup
levels, per docs/PROJECT_NOTES.md §8 Stage 2:

1. `byte_sha256` — sha256 of raw bytes, streamed in fixed-size chunks so the
   ~300MB fixture (docs/DATA_SPEC.md) never sits fully in memory.
2. text hash — sha256 of normalised extracted text (extractor/textextract.py
   + extractor/normalize.py). This is the actual `documents.id`: two files
   with different bytes but the same normalised text (different encodings,
   CRLF vs LF, an email that carries an invoice as its only real attachment)
   collapse into one document. When extraction fails, the document falls
   back to `identity_kind='bytes'` (byte_sha256 as the id) — this is also
   what lets two byte-identical corrupt files collapse into one quarantined
   document, per docs/DATA_SPEC.md's dedup-before-parsing recommendation.

Zip input: entries are only ever read through the zip handle (`zf.open`),
never extracted to a filesystem path built from the entry's own name — so
zip-slip has no attack surface here to begin with, not because entry names
are sanitised (they aren't; a `../../etc/passwd`-shaped name is stored as
plain TEXT in `files.path`, same as any other string, and is never joined
onto a filesystem path). The real risk with untrusted zip input is a zip
bomb (a small compressed stream declaring — or, in a malformed zip,
producing — an enormous decompressed size); that IS guarded by
MAX_ENTRY_BYTES, checked against the declared size before ever decompressing
and again against actual bytes read as the stream is consumed.

Extraction reads at most EXTRACTION_BUFFER_CAP bytes of any entry (streamed
and hashed in full regardless, for byte-identity purposes, up to
MAX_ENTRY_BYTES). That cap is exact and safe for TXT/HTML, which
textextract.py already treats as flat, prefix-truncatable formats. For
PDF/DOCX/EML it is a blunter tool: those formats need their full byte
structure (a zip central directory or a PDF xref table lives at the END of
the file), so a file of that kind larger than the cap would currently be
misread as corrupt rather than parsed. Every real document in this corpus is
well under the cap, and docs/PROJECT_NOTES.md itself assigns the correct
fix — "read PDFs page by page with a character cap" — to Stage 3, not here.
Known, disclosed gap.

No subprocess isolation or per-file timeout yet (also Stage 3): a
pathological file can still hang this today.
"""

from __future__ import annotations

import hashlib
import sqlite3
import unicodedata
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from extractor.formats import HEAD_SAMPLE_SIZE, Format, detect_format
from extractor.normalize import normalize_text
from extractor.textextract import ExtractionFailed, extract_text

HASH_CHUNK_SIZE = 1024 * 1024  # 1 MiB, per docs/PROJECT_NOTES.md §8
MAX_ENTRY_BYTES = 512 * 1024 * 1024  # zip-bomb / oversized-entry ceiling
EXTRACTION_BUFFER_CAP = 8 * 1024 * 1024  # how much of an entry we buffer for extraction


class _EntryTooLarge(Exception):
    pass


@dataclass(frozen=True)
class RawEntry:
    path: str
    size_hint: int
    oversized: bool
    open_stream: Callable[[], IO[bytes]] | None  # None only when oversized


@dataclass(frozen=True)
class ProcessedEntry:
    path: str
    format: Format
    byte_sha256: str
    size_bytes: int
    content_source: str  # 'own' | 'eml_attachment'
    identity: str
    identity_kind: str  # 'text' | 'bytes'
    quarantine_reason: str | None


@dataclass(frozen=True)
class InventoryStats:
    input_files: int
    unique_documents: int
    quarantined: int


def build_inventory(conn: sqlite3.Connection, input_path: Path) -> InventoryStats:
    """Scan `input_path` and upsert `files`/`documents`.

    Idempotent (`INSERT OR IGNORE` throughout): safe to call again on an
    existing db, including as part of a resumed run — Stage 7 does not need
    inventory itself to be incremental, only cheap enough to redo, which a
    directory walk + hashing pass is relative to an LLM call.
    """
    now = _iso_now()

    conn.execute("BEGIN IMMEDIATE")
    try:
        for raw in _iter_entries(input_path):
            processed = _process_entry(raw)
            _insert_document(conn, processed, now)
            _insert_file(conn, processed, now)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    (input_files,) = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    (unique_documents,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    (quarantined,) = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status = 'quarantined'"
    ).fetchone()
    return InventoryStats(input_files, unique_documents, quarantined)


def _iter_entries(input_path: Path) -> Iterator[RawEntry]:
    if input_path.is_dir():
        yield from _iter_directory(input_path)
    elif input_path.is_file() and zipfile.is_zipfile(input_path):
        yield from _iter_zip(input_path)
    else:
        raise ValueError(
            f"--input must be a directory or a zip archive, got: {input_path}"
        )


def _iter_directory(root: Path) -> Iterator[RawEntry]:
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = unicodedata.normalize("NFC", p.relative_to(root).as_posix())
        size = p.stat().st_size
        if size > MAX_ENTRY_BYTES:
            yield RawEntry(path=rel, size_hint=size, oversized=True, open_stream=None)
            continue
        yield RawEntry(
            path=rel,
            size_hint=size,
            oversized=False,
            open_stream=lambda p=p: p.open("rb"),
        )


def _iter_zip(archive_path: Path) -> Iterator[RawEntry]:
    with zipfile.ZipFile(archive_path) as zf:
        for zinfo in sorted(zf.infolist(), key=lambda zi: zi.filename):
            if zinfo.is_dir():
                continue
            name = unicodedata.normalize("NFC", zinfo.filename.replace("\\", "/"))
            if zinfo.file_size > MAX_ENTRY_BYTES:
                yield RawEntry(
                    path=name,
                    size_hint=zinfo.file_size,
                    oversized=True,
                    open_stream=None,
                )
                continue
            yield RawEntry(
                path=name,
                size_hint=zinfo.file_size,
                oversized=False,
                open_stream=lambda zi=zinfo: zf.open(zi),
            )


def _process_entry(entry: RawEntry) -> ProcessedEntry:
    if entry.oversized:
        return _quarantine_without_reading(entry, "corrupt_file")

    try:
        with entry.open_stream() as stream:
            byte_sha256, size_bytes, buffer = _hash_and_buffer(stream)
    except _EntryTooLarge:
        return _quarantine_without_reading(entry, "corrupt_file")
    except OSError:
        return _quarantine_without_reading(entry, "corrupt_file")

    fmt = detect_format(buffer[:HEAD_SAMPLE_SIZE])

    try:
        result = extract_text(fmt, buffer)
        normalized = normalize_text(result.text)
        if not normalized:
            raise ExtractionFailed("empty_text", "normalised text is empty")
    except ExtractionFailed as exc:
        return ProcessedEntry(
            path=entry.path,
            format=fmt,
            byte_sha256=byte_sha256,
            size_bytes=size_bytes,
            content_source="own",
            identity=byte_sha256,
            identity_kind="bytes",
            quarantine_reason=exc.reason,
        )
    except Exception:  # noqa: BLE001 — last-resort net; Stage 3 adds real subprocess isolation
        return ProcessedEntry(
            path=entry.path,
            format=fmt,
            byte_sha256=byte_sha256,
            size_bytes=size_bytes,
            content_source="own",
            identity=byte_sha256,
            identity_kind="bytes",
            quarantine_reason="corrupt_file",
        )

    identity = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return ProcessedEntry(
        path=entry.path,
        format=fmt,
        byte_sha256=byte_sha256,
        size_bytes=size_bytes,
        content_source=result.content_source,
        identity=identity,
        identity_kind="text",
        quarantine_reason=None,
    )


def _quarantine_without_reading(entry: RawEntry, reason: str) -> ProcessedEntry:
    # No content was ever read (too large to safely decompress/buffer), so
    # there is nothing content-based to hash. The path itself is the best
    # available identity: stable across resumes, conservative about not
    # deduping two different oversized files against each other.
    fallback_id = hashlib.sha256(entry.path.encode("utf-8")).hexdigest()
    return ProcessedEntry(
        path=entry.path,
        format=Format.UNKNOWN,
        byte_sha256=fallback_id,
        size_bytes=entry.size_hint,
        content_source="own",
        identity=fallback_id,
        identity_kind="bytes",
        quarantine_reason=reason,
    )


def _hash_and_buffer(stream: IO[bytes]) -> tuple[str, int, bytes]:
    hasher = hashlib.sha256()
    buffered: list[bytes] = []
    buffered_len = 0
    total = 0
    while True:
        chunk = stream.read(HASH_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_ENTRY_BYTES:
            raise _EntryTooLarge(total)
        hasher.update(chunk)
        if buffered_len < EXTRACTION_BUFFER_CAP:
            take = chunk[: EXTRACTION_BUFFER_CAP - buffered_len]
            buffered.append(take)
            buffered_len += len(take)
    return hasher.hexdigest(), total, b"".join(buffered)


def _insert_document(conn: sqlite3.Connection, entry: ProcessedEntry, now: str) -> None:
    status = "quarantined" if entry.quarantine_reason else "pending"
    conn.execute(
        "INSERT OR IGNORE INTO documents "
        "(id, identity_kind, status, quarantine_reason, summary, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '', ?, ?)",
        (
            entry.identity,
            entry.identity_kind,
            status,
            entry.quarantine_reason,
            now,
            now,
        ),
    )


def _insert_file(conn: sqlite3.Connection, entry: ProcessedEntry, now: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO files "
        "(path, format, byte_sha256, size_bytes, document_id, content_source, discovered_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            entry.path,
            entry.format.value,
            entry.byte_sha256,
            entry.size_bytes,
            entry.identity,
            entry.content_source,
            now,
        ),
    )


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
