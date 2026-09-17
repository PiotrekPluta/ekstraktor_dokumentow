"""Stage 8: `report` (docs/ZADANIE.md §3/§7, docs/PROJECT_NOTES.md §8).

Every number here comes from a `SELECT` over `documents`/`files`/`runs`/
`token_ledger` — never from counters accumulated during `run` itself. That is
what makes "the report balances" (requirement 7) true unconditionally,
including when the db was left mid-run by a `SIGKILL`: `input_files =
duplicate_files + unique_documents` holds by construction (`duplicate_files`
is defined as the difference, not measured independently), and
`unique_documents = processed_ok + quarantined + not_started` holds because
`documents.status` is a `CHECK`-constrained enum of exactly those buckets
(`in_progress` counted into `not_started` — see below).

Design decisions worth stating (docs/ZADANIE.md's "know what your solution
does not do"):

- `not_started` counts `pending` **and** `in_progress` rows together. A row
  sitting `in_progress` means a worker claimed it but this report can't tell
  whether the model call that would finish it actually happened — the same
  ambiguity `orchestrate._reset_in_progress` resolves on the next `run` by
  treating it as not-done. `report` makes the same call for consistency, so
  a report read *while* `run` is still in progress never double-counts a
  row as both "in flight" and "not started".
- `tokens_in`/`tokens_out` sum only `token_ledger` rows with `completed_at`
  set — a committed reservation for a call that never got a response (the
  process died mid-call) is real budget consumption (it must never be
  re-spent, see `orchestrate._sum_reserved_tokens`) but not real token
  *usage*, and requirement 7 asks for usage.
- `estimated_cost` is computed per run, at that run's own frozen
  `price_*_per_million` (see `db.py`'s `runs` table docstring), then summed
  — not at some single "current" rate re-read from a config file `report`
  never takes (`docs/ZADANIE.md` §3 gives `report` only `--db [--json]`).
- `backend`/`model`/`stop_reason` describe the **most recent** run row.
  If that row has no `ended_at`, the process never reached `_finish_run` —
  almost always a `SIGKILL` — and `stop_reason` is reported as
  `"interrupted"` even though the row's own column is `NULL`; `runs.
  stop_reason`'s `CHECK` constraint already allows this literal value,
  `orchestrate.py` just never has the chance to write it itself (the run
  that would need it is the one that got killed).
- `wall_time_s` sums each run's `ended_at - started_at` when known. For a
  run with no `ended_at` (the interrupted one, at most the last row), the
  true kill time is unrecoverable, so it is approximated by the latest
  `token_ledger` timestamp recorded for that run (`completed_at`, or
  `reserved_at` if no call finished) minus its `started_at` — a lower bound
  on wall time, not an exact figure. A run that was killed before reserving
  a single token contributes 0. This approximation is stated here and in
  `ARCHITECTURE.md`, not silently absorbed into "wall time was probably
  fine".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ReportData:
    input_files: int
    duplicate_files: int
    unique_documents: int
    processed_ok: int
    quarantined: int
    quarantine_by_reason: dict[str, int]
    not_started: int
    tokens_in: int
    tokens_out: int
    estimated_cost: float
    wall_time_s: float
    backend: str | None
    model: str | None
    stop_reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "input_files": self.input_files,
            "duplicate_files": self.duplicate_files,
            "unique_documents": self.unique_documents,
            "processed_ok": self.processed_ok,
            "quarantined": self.quarantined,
            "quarantine_by_reason": self.quarantine_by_reason,
            "not_started": self.not_started,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "estimated_cost": self.estimated_cost,
            "wall_time_s": self.wall_time_s,
            "backend": self.backend,
            "model": self.model,
            "stop_reason": self.stop_reason,
        }


def compute_report(conn: sqlite3.Connection) -> ReportData:
    (input_files,) = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    (unique_documents,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()

    (processed_ok,) = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status = 'done'"
    ).fetchone()
    (quarantined,) = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status = 'quarantined'"
    ).fetchone()
    (not_started,) = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status IN ('pending', 'in_progress')"
    ).fetchone()

    quarantine_by_reason = dict(
        conn.execute(
            "SELECT quarantine_reason, COUNT(*) FROM documents "
            "WHERE status = 'quarantined' GROUP BY quarantine_reason"
        ).fetchall()
    )

    (tokens_in, tokens_out) = conn.execute(
        "SELECT COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
        "FROM token_ledger WHERE completed_at IS NOT NULL"
    ).fetchone()

    backend, model, stop_reason = _latest_run_summary(conn)

    return ReportData(
        input_files=input_files,
        duplicate_files=input_files - unique_documents,
        unique_documents=unique_documents,
        processed_ok=processed_ok,
        quarantined=quarantined,
        quarantine_by_reason=quarantine_by_reason,
        not_started=not_started,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        estimated_cost=_estimated_cost(conn),
        wall_time_s=_wall_time_s(conn),
        backend=backend,
        model=model,
        stop_reason=stop_reason,
    )


def _estimated_cost(conn: sqlite3.Connection) -> float:
    rows = conn.execute(
        "SELECT r.price_input_per_million, r.price_output_per_million, "
        "       COALESCE(SUM(t.tokens_in), 0), COALESCE(SUM(t.tokens_out), 0) "
        "FROM runs r "
        "LEFT JOIN token_ledger t "
        "  ON t.run_id = r.id AND t.completed_at IS NOT NULL "
        "GROUP BY r.id"
    ).fetchall()
    total = 0.0
    for price_in, price_out, run_tokens_in, run_tokens_out in rows:
        total += run_tokens_in * price_in / 1_000_000
        total += run_tokens_out * price_out / 1_000_000
    return total


def _wall_time_s(conn: sqlite3.Connection) -> float:
    runs = conn.execute(
        "SELECT id, started_at, ended_at FROM runs ORDER BY id"
    ).fetchall()
    total = 0.0
    for run_id, started_at, ended_at in runs:
        start = _parse_iso(started_at)
        if ended_at is not None:
            end = _parse_iso(ended_at)
        else:
            end = _latest_ledger_timestamp(conn, run_id)
            if end is None:
                continue
        total += max((end - start).total_seconds(), 0.0)
    return total


def _latest_ledger_timestamp(conn: sqlite3.Connection, run_id: int) -> datetime | None:
    (value,) = conn.execute(
        "SELECT MAX(x) FROM ("
        "  SELECT completed_at AS x FROM token_ledger "
        "  WHERE run_id = ? AND completed_at IS NOT NULL "
        "  UNION ALL "
        "  SELECT reserved_at AS x FROM token_ledger WHERE run_id = ?"
        ")",
        (run_id, run_id),
    ).fetchone()
    return _parse_iso(value) if value is not None else None


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _latest_run_summary(
    conn: sqlite3.Connection,
) -> tuple[str | None, str | None, str | None]:
    row = conn.execute(
        "SELECT backend, model, stop_reason, ended_at FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None, None, None
    backend, model, stop_reason, ended_at = row
    if ended_at is None:
        stop_reason = "interrupted"
    return backend, model, stop_reason


def format_report_text(data: ReportData) -> str:
    lines = [
        f"input_files       = {data.input_files}",
        f"duplicate_files   = {data.duplicate_files}",
        f"unique_documents  = {data.unique_documents}",
        f"processed_ok      = {data.processed_ok}",
        f"quarantined       = {data.quarantined}",
        f"not_started       = {data.not_started}",
    ]
    if data.quarantine_by_reason:
        lines.append("quarantine_by_reason:")
        for reason, count in sorted(data.quarantine_by_reason.items()):
            lines.append(f"  {reason:<20} {count}")
    lines += [
        f"tokens_in         = {data.tokens_in}",
        f"tokens_out        = {data.tokens_out}",
        f"estimated_cost    = {data.estimated_cost:.6f}",
        f"wall_time_s       = {data.wall_time_s:.3f}",
        f"backend           = {data.backend}",
        f"model             = {data.model}",
        f"stop_reason       = {data.stop_reason}",
    ]
    return "\n".join(lines)
