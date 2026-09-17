"""Stage 9's centerpiece acceptance test (docs/ZADANIE.md requirement 4,
docs/PROJECT_NOTES.md §8 Stage 9): a real `SIGKILL` against a real, spawned
`extractor run` **process** — not the in-process simulation
test_orchestrate.py's resumability tests use (thread-pool workers, no real
process boundary, nothing un-catchable). Those tests exercise the
resumability *logic* in isolation; this one exercises the actual guarantee
the brief makes about the command-line tool itself surviving a kill signal
that cannot be caught or cleaned up after.

Uses the real `extractor.cli` entry point via `python -m extractor.cli`
(the `fake` backend, requirement 10 — no network/model/API key), not
`uv run extractor` — same interpreter (`sys.executable`, already the venv
pytest itself runs under), avoids `uv`'s own per-invocation overhead on
top of Python's.

`[backend.fake] delay_s` (new in `extractor.llm.fake.FakeLLMClient`, wired
through `build_client`) exists solely for this test: with no artificial
delay the fake backend has no real I/O at all, and a handful of documents
can finish before an external poll loop run from a *different* process
even observes the first completion, making "kill after N completions, N <
total" impossible to hit reliably.

The two things checked are deliberately separate, per Stage 9's own
wording ("compare the record set against a clean run" AND "assert no
repeated model calls for completed documents") — with a fully
deterministic fake backend, comparing only the final record set could not,
by itself, catch a bug that silently reprocessed an already-completed
document, since reprocessing would produce the exact same deterministic
result. Only counting `token_ledger` rows (this tool's durable record of
"a model call happened") catches that.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

_POLL_INTERVAL_S = 0.02
_POLL_TIMEOUT_S = 15.0
_WAIT_TIMEOUT_S = 20.0

_DOCUMENT_COLUMNS = (
    "id, status, quarantine_reason, doc_type, counterparty_name, "
    "counterparty_tax_id, issue_date, due_date, gross_amount, currency, summary"
)


def _write_input(input_dir: Path, n: int) -> None:
    input_dir.mkdir()
    for i in range(n):
        (input_dir / f"doc{i}.txt").write_text(
            f"Faktura testowa numer {i}, kwota {100 + i} zl.", encoding="utf-8"
        )


def _write_fake_config(path: Path, *, delay_s: float) -> None:
    path.write_text(
        "[run]\n"
        "workers = 1\n"
        "\n"
        '[backend]\nname = "fake"\n'
        "\n"
        f"[backend.fake]\ndelay_s = {delay_s}\n",
        encoding="utf-8",
    )


def _spawn_run(input_dir: Path, db_path: Path, config_path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "extractor.cli",
            "run",
            "--input",
            str(input_dir),
            "--db",
            str(db_path),
            "--config",
            str(config_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _quarantined_ids(db_path: Path) -> list[str]:
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return []
    try:
        return [
            row[0]
            for row in conn.execute(
                "SELECT id FROM documents WHERE status = 'quarantined' ORDER BY id"
            ).fetchall()
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _document_snapshot(db_path: Path) -> dict[str, tuple]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_DOCUMENT_COLUMNS} FROM documents ORDER BY id"
        ).fetchall()
        return {row[0]: tuple(row) for row in rows}
    finally:
        conn.close()


def _completed_call_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT document_id, COUNT(*) FROM token_ledger "
            "WHERE completed_at IS NOT NULL GROUP BY document_id"
        ).fetchall()
        return dict(rows)
    finally:
        conn.close()


def _wait_or_kill(proc: subprocess.Popen, timeout: float) -> None:
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        raise


def test_real_sigkill_mid_run_then_resume_matches_a_clean_run(tmp_path: Path) -> None:
    n_docs = 4
    input_dir = tmp_path / "in"
    _write_input(input_dir, n_docs)
    config_path = tmp_path / "fake.toml"
    _write_fake_config(config_path, delay_s=0.15)

    killed_db = tmp_path / "killed.sqlite"
    proc = _spawn_run(input_dir, killed_db, config_path)
    try:
        deadline = time.monotonic() + _POLL_TIMEOUT_S
        pre_kill_ids: list[str] = []
        while time.monotonic() < deadline:
            pre_kill_ids = _quarantined_ids(killed_db)
            if pre_kill_ids:
                break
            assert proc.poll() is None, (
                "extractor run exited before completing a single document: "
                f"{proc.stdout.read().decode(errors='replace') if proc.stdout else ''}"
            )
            time.sleep(_POLL_INTERVAL_S)
        assert pre_kill_ids, "timed out waiting for the first completed document"
        assert len(pre_kill_ids) < n_docs, (
            "the whole run finished before the kill could land — not a real "
            "mid-run interruption; increase delay_s or n_docs"
        )

        pre_kill_snapshot = _document_snapshot(killed_db)
        pre_kill_calls = _completed_call_counts(killed_db)

        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        assert proc.returncode == -signal.SIGKILL
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    # Resume: identical command, identical (now-recovering) db.
    resume_proc = _spawn_run(input_dir, killed_db, config_path)
    _wait_or_kill(resume_proc, _WAIT_TIMEOUT_S)
    assert resume_proc.returncode == 0

    post_resume_calls = _completed_call_counts(killed_db)
    for doc_id in pre_kill_ids:
        assert post_resume_calls[doc_id] == pre_kill_calls[doc_id], (
            f"document {doc_id} was already completed before the kill but "
            "gained additional model calls after resume"
        )

    post_resume_snapshot = _document_snapshot(killed_db)
    for doc_id in pre_kill_ids:
        assert post_resume_snapshot[doc_id] == pre_kill_snapshot[doc_id], (
            f"document {doc_id}'s recorded result changed across the resume"
        )

    # A clean, uninterrupted run over the identical input/config must land
    # on exactly the same final record set (docs/ZADANIE.md requirement 4).
    clean_db = tmp_path / "clean.sqlite"
    clean_proc = _spawn_run(input_dir, clean_db, config_path)
    _wait_or_kill(clean_proc, _WAIT_TIMEOUT_S)
    assert clean_proc.returncode == 0

    assert post_resume_snapshot == _document_snapshot(clean_db)
