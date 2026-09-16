"""Stage 2/3: inventory, deduplication, and hardened extraction.

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

Extraction hardening (Stage 3): every extraction attempt runs in its own
subprocess (`multiprocessing`, forced `spawn` context regardless of
platform — macOS defaults to it, docs/PROJECT_NOTES.md §7, and forcing it
locally too surfaces pickling bugs in dev rather than in front of the
reviewer) with a wall-clock timeout. A hang past the timeout gets the
process killed and the document quarantined as `parse_timeout`; a crash
(segfault, unhandled exception escaping the worker) is `corrupt_file`.
Nothing at the extractor-function level can enforce this — a stuck C
call inside pypdfium2 does not yield to a Python-level timeout — which is
why it lives one layer up, wrapping extraction rather than being part of it.

What each format needs from the entry's bytes also drives how they're
materialised for extraction: directory input already has a real path, so
extraction reads it directly and natively (pdfium/python-docx manage their
own memory-efficient, non-truncated access — no more read-then-cap step
that could corrupt a large PDF's xref table or a large DOCX's central
directory). Zip input has no standalone path; PDF/DOCX entries from a zip
are spooled to a temp file (bounded by MAX_ENTRY_BYTES, same zip-bomb
ceiling) so they get that same native, non-truncated access, while
TXT/HTML/EML — flat formats where a bounded prefix is genuinely all
extraction ever uses — stay a bounded in-memory buffer, no disk I/O needed.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import sqlite3
import tempfile
import time
import unicodedata
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty
from typing import IO

from extractor.formats import HEAD_SAMPLE_SIZE, Format, detect_format
from extractor.normalize import normalize_text
from extractor.textextract import ExtractionFailed, extract_text

HASH_CHUNK_SIZE = 1024 * 1024  # 1 MiB, per docs/PROJECT_NOTES.md §8
MAX_ENTRY_BYTES = 512 * 1024 * 1024  # zip-bomb / oversized-entry ceiling
EXTRACTION_BUFFER_CAP = 8 * 1024 * 1024  # in-memory cap for zip-sourced TXT/HTML/EML
DEFAULT_EXTRACTION_TIMEOUT_S = 30.0

# Formats materialised to a real file (native path access) rather than a
# bounded in-memory buffer — see module docstring.
_PATH_NATIVE_FORMATS = {Format.PDF, Format.DOCX}


class _EntryTooLarge(Exception):
    pass


@dataclass(frozen=True)
class RawEntry:
    path: str
    size_hint: int
    oversized: bool
    open_stream: Callable[[], IO[bytes]] | None  # None only when oversized
    real_path: Path | None  # set for directory input; None for zip entries


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
    # Original (pre-normalize_text) extracted text, for documents.source_text
    # (Stage 7): None exactly when quarantine_reason is set — nothing was
    # ever successfully extracted to store.
    source_text: str | None


@dataclass(frozen=True)
class InventoryStats:
    input_files: int
    unique_documents: int
    quarantined: int


def build_inventory(
    conn: sqlite3.Connection,
    input_path: Path,
    timeout_s: float = DEFAULT_EXTRACTION_TIMEOUT_S,
) -> InventoryStats:
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
            processed = _process_entry(raw, timeout_s)
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
            yield RawEntry(
                path=rel,
                size_hint=size,
                oversized=True,
                open_stream=None,
                real_path=None,
            )
            continue
        yield RawEntry(
            path=rel,
            size_hint=size,
            oversized=False,
            open_stream=lambda p=p: p.open("rb"),
            real_path=p,
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
                    real_path=None,
                )
                continue
            yield RawEntry(
                path=name,
                size_hint=zinfo.file_size,
                oversized=False,
                open_stream=lambda zi=zinfo: zf.open(zi),
                real_path=None,
            )


def _process_entry(entry: RawEntry, timeout_s: float) -> ProcessedEntry:
    if entry.oversized:
        return _quarantine_without_reading(entry, "corrupt_file")

    try:
        byte_sha256, size_bytes, fmt, source, cleanup_path = _materialize_entry(entry)
    except (_EntryTooLarge, OSError):
        return _quarantine_without_reading(entry, "corrupt_file")

    try:
        text, raw_text, content_source, reason = _extract_via_subprocess(
            fmt, source, timeout_s
        )
    finally:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)

    if reason is not None:
        return ProcessedEntry(
            path=entry.path,
            format=fmt,
            byte_sha256=byte_sha256,
            size_bytes=size_bytes,
            content_source="own",
            identity=byte_sha256,
            identity_kind="bytes",
            quarantine_reason=reason,
            source_text=None,
        )

    identity = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ProcessedEntry(
        path=entry.path,
        format=fmt,
        byte_sha256=byte_sha256,
        size_bytes=size_bytes,
        content_source=content_source,
        identity=identity,
        identity_kind="text",
        quarantine_reason=None,
        source_text=raw_text,
    )


def _materialize_entry(
    entry: RawEntry,
) -> tuple[str, int, Format, bytes | Path, Path | None]:
    """Hash the whole entry (bounded by MAX_ENTRY_BYTES) and produce
    whatever extraction needs: the real path for directory input, a spooled
    temp file for zip-sourced PDF/DOCX, or a bounded in-memory buffer for
    everything else. Returns (byte_sha256, size_bytes, format,
    extraction_source, temp_path_to_clean_up_or_None).
    """
    with entry.open_stream() as stream:
        hasher = hashlib.sha256()
        head = stream.read(HEAD_SAMPLE_SIZE)
        hasher.update(head)
        fmt = detect_format(head)
        total = len(head)
        if total > MAX_ENTRY_BYTES:
            # Only reachable if MAX_ENTRY_BYTES is configured below
            # HEAD_SAMPLE_SIZE — never happens with the 512MB default, but
            # the streaming loops below only check *subsequent* reads, so
            # this guards the initial one uniformly rather than leaving a
            # gap for whatever future config wires this constant down to.
            raise _EntryTooLarge(total)

        if entry.real_path is not None:
            total = _hash_rest(stream, hasher, total)
            return hasher.hexdigest(), total, fmt, entry.real_path, None

        if fmt in _PATH_NATIVE_FORMATS:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = Path(tmp.name)
                try:
                    tmp.write(head)
                    total = _hash_rest(stream, hasher, total, sink=tmp)
                except BaseException:
                    # A mid-spool failure (e.g. _EntryTooLarge) must not
                    # leak the partial file — this is the only place that
                    # knows its path before _process_entry's own cleanup
                    # ever gets a chance to run.
                    tmp_path.unlink(missing_ok=True)
                    raise
            return hasher.hexdigest(), total, fmt, tmp_path, tmp_path

        buffered = [head]
        buffered_len = len(head)
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
        return hasher.hexdigest(), total, fmt, b"".join(buffered), None


def _hash_rest(
    stream: IO[bytes], hasher: hashlib._Hash, total: int, sink: IO[bytes] | None = None
) -> int:
    while True:
        chunk = stream.read(HASH_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_ENTRY_BYTES:
            raise _EntryTooLarge(total)
        hasher.update(chunk)
        if sink is not None:
            sink.write(chunk)
    return total


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
        source_text=None,
    )


# --- extraction, isolated in a subprocess with a hard timeout -------------


def _extract_via_subprocess(
    fmt: Format, source: bytes | Path, timeout_s: float
) -> tuple[str | None, str | None, str, str | None]:
    """Returns (normalised_text, raw_text, content_source, quarantine_reason)
    where quarantine_reason is None on success and normalised_text/raw_text
    are None otherwise. `raw_text` is the pre-normalize_text() extraction
    result (documents.source_text, Stage 7) — kept alongside the normalised
    identity text rather than instead of it, since windowing/validation want
    case and structure preserved while identity hashing wants it stripped.
    """
    arg_source: bytes | str = (
        source if isinstance(source, (bytes, bytearray)) else str(source)
    )
    outcome = _run_with_timeout(
        _extract_worker, (fmt.value, arg_source), timeout_s=timeout_s
    )

    if outcome[0] == "timeout":
        return None, None, "own", "parse_timeout"
    if outcome[0] == "crashed":
        return None, None, "own", "corrupt_file"

    payload = outcome[1]
    if payload is None:
        # Process exited cleanly but put nothing on the queue — treat the
        # same as a crash rather than trust an empty result.
        return None, None, "own", "corrupt_file"
    if payload[0] == "ok":
        _, text, raw_text, content_source = payload
        return text, raw_text, content_source, None
    _, reason = payload
    return None, None, "own", reason


def _run_with_timeout(
    target: Callable[..., None], args: tuple, timeout_s: float
) -> tuple[str, object]:
    """Generic "run this in an isolated, killable subprocess" primitive.
    Kept decoupled from extraction semantics so it's directly testable with
    a trivial target, independent of real parsing behaviour.

    Returns ("ok", queue_payload_or_None), ("timeout", None), or
    ("crashed", exitcode).

    Reads the queue *before* joining, deliberately: a child whose payload
    exceeds the pipe's OS buffer (a large PDF's extracted text easily does)
    blocks inside `queue.put()` until something reads it. Joining first
    would deadlock — the parent waiting for exit, the child waiting to be
    read — https://docs.python.org/3/library/multiprocessing.html#pipes-and-queues.
    """
    ctx = multiprocessing.get_context("spawn")
    queue: multiprocessing.Queue = ctx.Queue()
    process = ctx.Process(target=target, args=(*args, queue), daemon=True)
    process.start()

    deadline = time.monotonic() + timeout_s
    payload = None
    got_payload = False
    while time.monotonic() < deadline:
        try:
            payload = queue.get(timeout=0.1)
            got_payload = True
            break
        except Empty:
            if not process.is_alive():
                break  # exited without ever calling put() — a crash

    if not got_payload:
        if process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
            process.join()
            return "timeout", None
        process.join()
        return "crashed", process.exitcode

    process.join(5)
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
        process.join()

    if process.exitcode != 0:
        return "crashed", process.exitcode
    return "ok", payload


def _extract_worker(
    fmt_value: str, source: bytes | str, queue: multiprocessing.Queue
) -> None:
    """Runs in the child process (spawned fresh — re-imports everything,
    no state inherited from the parent). Must stay a top-level function:
    `spawn` pickles it by qualified name.
    """
    real_source: bytes | Path = (
        source if isinstance(source, (bytes, bytearray)) else Path(source)
    )
    fmt = Format(fmt_value)
    try:
        result = extract_text(fmt, real_source)
        normalized = normalize_text(result.text)
        if not normalized:
            raise ExtractionFailed("empty_text", "normalised text is empty")
        queue.put(("ok", normalized, result.text, result.content_source))
    except ExtractionFailed as exc:
        queue.put(("failed", exc.reason))
    except Exception:  # noqa: BLE001 — last-resort net; a crash here must
        # still report a reason, not vanish the document.
        queue.put(("failed", "corrupt_file"))


def _insert_document(conn: sqlite3.Connection, entry: ProcessedEntry, now: str) -> None:
    status = "quarantined" if entry.quarantine_reason else "pending"
    conn.execute(
        "INSERT OR IGNORE INTO documents "
        "(id, identity_kind, status, quarantine_reason, summary, source_text, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '', ?, ?, ?)",
        (
            entry.identity,
            entry.identity_kind,
            status,
            entry.quarantine_reason,
            entry.source_text,
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
