"""SQLite schema and connection setup.

Four tables, per docs/PROJECT_NOTES.md §8 Stage 7:

- `runs` — one row per `extractor run` invocation (workers/limit/budget
  requested, backend+model resolved, how it stopped).
- `documents` — one row per *unique* document (after dedup), keyed by the
  dedup identity itself rather than a surrogate integer. See "Document
  identity" below.
- `files` — one row per input file discovered, pointing at the document it
  resolves to. Duplicates are files whose document_id is shared with another
  row; the report's `duplicate_files` count falls out of that by
  arithmetic (input_files - unique_documents), so it is not stored directly.
  `content_source` is `'eml_attachment'` when an `.eml` file's identity and
  text came from an attachment rather than its own body (an email
  forwarding an invoice IS that invoice) — `path` stays the `.eml`'s own
  real path either way, so `eval`'s join against `expected.jsonl` never
  needs a synthetic path; this column exists purely for `sqlite3`
  inspectability.
- `token_ledger` — append-only. One row per LLM call *attempt* (reservation
  committed before the call, actual usage filled in after the response), so
  a process killed mid-call leaves a conservative, never-exceeded budget
  record. Whether budget/limit are enforced per-invocation or cumulatively
  across every run recorded in this db is an orchestration policy decision
  (docs/PROJECT_NOTES.md §3, open question 2) — this schema supports either,
  since both are just different aggregations over `token_ledger`/`runs`.

Document identity
------------------
`documents.id` is not a surrogate key. It is the dedup identity computed by
Stage 2: the sha256 of normalised text when text extraction succeeded
(`identity_kind = 'text'`), or the sha256 of raw bytes when it did not
(`identity_kind = 'bytes'` — corrupt/unsupported/empty files, which is also
what lets two byte-identical corrupt files collapse into one document, per
docs/DATA_SPEC.md's dedup-before-parsing recommendation). Using the hash
itself as the primary key means "processing order is deterministic, sorted
by document id" (docs/ZADANIE.md §3) falls out of `ORDER BY id` for free,
with no separate sequence to keep in sync across resumes.

Status lifecycle: pending -> in_progress -> done | quarantined. On startup,
orchestration resets any `in_progress` row back to `pending` — a row in that
state means a worker claimed it but we cannot tell whether the model call
that would complete it actually happened, so it must be treated as not done
(docs/ZADANIE.md §4: no repeated model calls for documents already `done`).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    stop_reason     TEXT
                        CHECK (stop_reason IS NULL OR stop_reason IN (
                            'completed', 'limit_reached', 'budget_exhausted',
                            'backend_unavailable', 'interrupted'
                        )),
    workers         INTEGER NOT NULL,
    limit_docs      INTEGER,
    budget_tokens   INTEGER,
    backend         TEXT NOT NULL,
    model           TEXT NOT NULL,
    config_path     TEXT NOT NULL,
    input_path      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id                      TEXT PRIMARY KEY,
    identity_kind           TEXT NOT NULL CHECK (identity_kind IN ('text', 'bytes')),
    status                  TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'in_progress', 'done', 'quarantined')),
    quarantine_reason       TEXT
                                CHECK (quarantine_reason IS NULL OR quarantine_reason IN (
                                    'corrupt_file', 'unsupported_format', 'empty_text',
                                    'no_text_layer', 'parse_timeout', 'llm_invalid_output'
                                )),
    doc_type                TEXT
                                CHECK (doc_type IS NULL OR doc_type IN (
                                    'invoice', 'contract', 'offer', 'correspondence', 'other'
                                )),
    counterparty_name       TEXT,
    counterparty_tax_id     TEXT,
    issue_date              TEXT,
    due_date                TEXT,
    gross_amount            TEXT,
    currency                TEXT,
    summary                 TEXT NOT NULL DEFAULT '',
    backend                 TEXT,
    model                   TEXT,
    run_id                  INTEGER REFERENCES runs(id),
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    CHECK (
        (status = 'quarantined' AND quarantine_reason IS NOT NULL)
        OR (status != 'quarantined' AND quarantine_reason IS NULL)
    ),
    CHECK (status != 'done' OR doc_type IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

CREATE TABLE IF NOT EXISTS files (
    path            TEXT PRIMARY KEY,
    format          TEXT NOT NULL CHECK (format IN ('pdf', 'docx', 'html', 'eml', 'txt', 'unknown')),
    byte_sha256     TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    document_id     TEXT NOT NULL REFERENCES documents(id),
    content_source  TEXT NOT NULL DEFAULT 'own'
                        CHECK (content_source IN ('own', 'eml_attachment')),
    discovered_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_document_id ON files(document_id);
CREATE INDEX IF NOT EXISTS idx_files_byte_sha256 ON files(byte_sha256);

CREATE TABLE IF NOT EXISTS token_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    document_id     TEXT NOT NULL REFERENCES documents(id),
    attempt         INTEGER NOT NULL DEFAULT 1,
    reserved_at     TEXT NOT NULL,
    reserved_tokens INTEGER NOT NULL,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    completed_at    TEXT,
    UNIQUE (run_id, document_id, attempt)
);

CREATE INDEX IF NOT EXISTS idx_token_ledger_run_id ON token_ledger(run_id);
CREATE INDEX IF NOT EXISTS idx_token_ledger_document_id ON token_ledger(document_id);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the result database with the schema applied.

    Autocommit (`isolation_level=None`): callers open transactions explicitly
    (`BEGIN IMMEDIATE` / `COMMIT`) around each unit of work, e.g. "write this
    document's result", so requirement 8 (a document's content can only ever
    affect its own row) maps onto a single parameterised statement inside a
    single transaction rather than relying on driver-implicit ones.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
