"""Stage 7: orchestration, resume, and budget (docs/PROJECT_NOTES.md §8,
"the core of the task").

`run()` is what `extractor.cli`'s `run` command calls, after
`extractor.inventory.build_inventory` has already populated `documents`.
It turns the building blocks from every earlier stage — windowing (4), the
LLM client (5), validation (6) — into the actual per-document loop: claim a
pending document in deterministic order, build a prompt from its windowed
context, call the model, validate the response (one repair attempt on
invalid JSON), write the result — all inside transactions scoped to that
one document's own row (requirement 8), so that a `SIGKILL` at any point
leaves the db in a state `run()` can resume from exactly (requirement 4),
and `--workers 1` and `--workers 16` produce the same final record set
(requirement 5).

Key design decisions (full reasoning: docs/plan/Stage_7_plan.md):

- `--limit`/`--budget` are cumulative across the whole db's history, not
  reset per invocation — the only reading consistent with "the record set
  after any number of interruptions matches an uninterrupted run" holding
  unconditionally. `--limit` is enforced structurally: the target-id list
  handed to workers is computed once, up front, sized to
  `limit - already_attempted`, so it's physically impossible to claim more
  documents than the limit allows in this run, regardless of `--workers` or
  how many of those claims later fail and go back to `pending`. `--budget`
  can't be pre-sized the same way (token cost depends on content), so it's
  enforced live: a shared, lock-guarded `_Accountant` tracks cumulative
  reserved tokens and is checked before every reservation, stopping the run
  before a reservation would exceed the budget — never after.
- Threads, not processes: the per-document work here is dominated by an
  HTTP call to the model server (I/O-bound), unlike Stage 3's CPU/C-library
  -bound extraction subprocesses. One `ResilientLLMClient` (and its one
  `CircuitBreaker`) is shared across every worker thread — a global "backend
  is down" signal, not one breaker per worker that would each independently
  need `failure_threshold` failures to notice the same outage.
- `llm_invalid_output` is reused for "no usable response after retries",
  not a new quarantine reason: two paths lead there (JSON still invalid
  after one repair attempt; `ResilientLLMClient.complete()` raised after
  exhausting retries without tripping the breaker), both meaning "this
  document did not get a valid extraction" from the document's own point of
  view.
- Reservations stay conservative across resumes: a `token_ledger` row is
  inserted *before* the call and never released, even if the process is
  killed before the response comes back, or the document turns out to need
  a repair attempt that itself can't be afforded. `SUM(reserved_tokens)`
  therefore never undercounts, matching requirement 6 literally ("the tool
  ends before the budget would be exceeded").
"""

from __future__ import annotations

import queue
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from extractor.config import Config
from extractor.db import connect
from extractor.llm.client import (
    LLMBackendUnavailable,
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
)
from extractor.prompt import build_prompt, build_repair_prompt
from extractor.schema import ExtractedFields
from extractor.tokenizer import count_tokens as _tokenizer_count_tokens
from extractor.tokenizer import load_tokenizer
from extractor.validation import ValidationOutcome, validate_extraction
from extractor.windowing import build_context

_JSON_SCHEMA = ExtractedFields.model_json_schema()

# Fallback when a backend declares no context_tokens (the `fake` backend has
# no real --ctx-size to respect) — generous, matching windowing's own
# DEFAULT_TOKEN_BUDGET-scale default rather than constraining fake-backend
# tests for no reason.
_DEFAULT_CONTEXT_TOKENS = 100_000
_SAFETY_MARGIN_TOKENS = 64
_MIN_CONTEXT_TOKEN_BUDGET = 200


@dataclass(frozen=True)
class RunResult:
    run_id: int
    stop_reason: str


def default_token_counter() -> Callable[[str], int]:
    """The real, offline counter (ARCHITECTURE.md's "nothing wires either
    counter through by default yet" — this is that wiring). Loaded once per
    `run()` call, not per document: `assets/tokenizer.json` is ~3.7MB and
    re-parsing it per document would be pure waste. Shared read-only across
    every worker thread — the `tokenizers` library's `Tokenizer.encode` has
    no mutable per-call state, so concurrent use is safe.
    """
    tokenizer = load_tokenizer()
    return lambda text: _tokenizer_count_tokens(tokenizer, text)


def _context_window_budget(config: Config, count_tokens: Callable[[str], int]) -> int:
    """How many tokens of *document context* `windowing.build_context` may
    use, sized so that context + fixed prompt/repair overhead + up to two
    `max_output_tokens`-sized completions (the repair turn echoes the
    model's own previous response back, on top of its own new completion
    cap) still fits inside the backend's configured `--ctx-size`.

    Found necessary by a real-fixture run against the pinned llama-server
    (CLAUDE.md's "verify against real fixtures" rule): the system prompt
    alone costs ~1000 tokens, and without sizing the context budget around
    that, requests routinely exceeded the server's 2048-token context and
    were rejected outright (`LLMConnectionError`) rather than ever reaching
    validation. `count_tokens` is the same counter used everywhere else in
    this run, so the estimate is consistent with what the reservation and
    the windowing call itself use.
    """
    backend_cfg = config.raw.get("backend", {}).get(config.backend, {})
    context_tokens = backend_cfg.get("context_tokens", _DEFAULT_CONTEXT_TOKENS)

    fixed_overhead = max(
        count_tokens(build_prompt("")),
        count_tokens(build_repair_prompt("", "", "")),
    )
    budget = (
        context_tokens
        - fixed_overhead
        - 2 * config.max_output_tokens
        - _SAFETY_MARGIN_TOKENS
    )
    return max(budget, _MIN_CONTEXT_TOKEN_BUDGET)


def run(
    db_path: Path,
    input_path: Path,
    client: LLMClient,
    config: Config,
    *,
    config_path: str,
    count_tokens: Callable[[str], int] | None = None,
) -> RunResult:
    """Opens its own connection(s) — the caller (`extractor.cli`) is only
    responsible for having already run `inventory.build_inventory` against
    `db_path` so there's `documents` rows to claim.
    """
    conn = connect(db_path)
    try:
        return _run(
            conn, db_path, input_path, client, config, config_path, count_tokens
        )
    finally:
        conn.close()


def _run(
    conn: sqlite3.Connection,
    db_path: Path,
    input_path: Path,
    client: LLMClient,
    config: Config,
    config_path: str,
    count_tokens: Callable[[str], int] | None,
) -> RunResult:
    if count_tokens is None:
        count_tokens = default_token_counter()
    context_budget = _context_window_budget(config, count_tokens)

    now = _iso_now()
    _reset_in_progress(conn, now)

    backend_name = config.backend
    model_name = _resolve_model_tag(config)
    price_in, price_out = _resolve_prices(config, backend_name)
    run_id = _insert_run(
        conn,
        now,
        config,
        backend_name,
        model_name,
        config_path,
        str(input_path),
        price_in,
        price_out,
    )

    already_attempted = _count_attempted(conn)
    already_reserved = _sum_reserved_tokens(conn)
    remaining_limit = (
        max(config.limit - already_attempted, 0) if config.limit is not None else None
    )

    (pending_count,) = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status = 'pending'"
    ).fetchone()
    if remaining_limit is not None:
        rows = conn.execute(
            "SELECT id FROM documents WHERE status = 'pending' ORDER BY id LIMIT ?",
            (remaining_limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM documents WHERE status = 'pending' ORDER BY id"
        ).fetchall()
    target_ids = [row[0] for row in rows]
    limit_would_truncate = remaining_limit is not None and pending_count > len(
        target_ids
    )

    accountant = _Accountant(reserved_tokens=already_reserved, budget=config.budget)

    if target_ids:
        work_queue: queue.Queue = queue.Queue()
        for doc_id in target_ids:
            work_queue.put(doc_id)

        with ThreadPoolExecutor(max_workers=config.workers) as executor:
            futures = [
                executor.submit(
                    _worker_loop,
                    work_queue,
                    db_path,
                    client,
                    config,
                    accountant,
                    run_id,
                    backend_name,
                    model_name,
                    count_tokens,
                    context_budget,
                )
                for _ in range(config.workers)
            ]
            for f in futures:
                f.result()

    if accountant.stop_reason is not None:
        stop_reason = accountant.stop_reason
    elif limit_would_truncate:
        stop_reason = "limit_reached"
    else:
        stop_reason = "completed"

    _finish_run(conn, run_id, stop_reason)
    return RunResult(run_id=run_id, stop_reason=stop_reason)


class _Accountant:
    """Shared, lock-guarded run-level stop condition: cumulative reserved
    tokens (budget) and whether the backend has been declared unavailable.
    `--limit` is *not* tracked here — it's enforced structurally by the
    fixed size of the target-id queue (see module docstring), which needs
    no runtime bookkeeping at all.
    """

    def __init__(self, reserved_tokens: int, budget: int | None) -> None:
        self._lock = threading.Lock()
        self._reserved_tokens = reserved_tokens
        self._budget = budget
        self.stop_reason: str | None = None

    def try_reserve(self, tokens: int) -> bool:
        with self._lock:
            if self.stop_reason is not None:
                return False
            if (
                self._budget is not None
                and self._reserved_tokens + tokens > self._budget
            ):
                self.stop_reason = "budget_exhausted"
                return False
            self._reserved_tokens += tokens
            return True

    def note_backend_unavailable(self) -> None:
        with self._lock:
            if self.stop_reason is None:
                self.stop_reason = "backend_unavailable"


class _RunStopped(Exception):
    """Internal signal: this document's attempt was aborted because a
    run-level stop condition (budget or backend availability) fired during
    it. The document itself has already been left in the right state
    (released back to `pending`) by whoever raised this — callers just stop
    processing it and let the worker loop's own stop check end the run.
    """


@dataclass(frozen=True)
class _AttemptResult:
    # None means the LLM call itself failed after exhausting retries
    # (ResilientLLMClient raised a non-`LLMBackendUnavailable` LLMError) —
    # a document-scoped failure distinct from an invalid-JSON response,
    # which validate_extraction reports via ValidationOutcome instead.
    outcome: ValidationOutcome | None
    response_text: str | None


def _worker_loop(
    work_queue: queue.Queue,
    db_path: Path,
    client: LLMClient,
    config: Config,
    accountant: _Accountant,
    run_id: int,
    backend_name: str,
    model_name: str,
    count_tokens: Callable[[str], int],
    context_budget: int,
) -> None:
    """Runs in one worker thread with its own sqlite3 connection — WAL mode
    plus `busy_timeout` (extractor.db.connect) serialises conflicting
    writers rather than erroring, so no thread ever borrows another's
    connection.
    """
    conn = connect(db_path)
    try:
        while True:
            if accountant.stop_reason is not None:
                return
            try:
                doc_id = work_queue.get_nowait()
            except queue.Empty:
                return
            _process_document(
                conn,
                doc_id,
                client,
                config,
                accountant,
                run_id,
                backend_name,
                model_name,
                count_tokens,
                context_budget,
            )
    finally:
        conn.close()


def _process_document(
    conn: sqlite3.Connection,
    doc_id: str,
    client: LLMClient,
    config: Config,
    accountant: _Accountant,
    run_id: int,
    backend_name: str,
    model_name: str,
    count_tokens: Callable[[str], int],
    context_budget: int,
) -> None:
    if not _claim(conn, doc_id, _iso_now()):
        return  # claimed/finished elsewhere already — defensive, not expected

    row = conn.execute(
        "SELECT source_text FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    source_text = row[0] if row is not None else None
    if not source_text:
        # A 'pending' document should always have source_text — only
        # identity_kind='bytes' rows (quarantined at inventory time) lack
        # it, and those never become 'pending'. Quarantine rather than
        # crash the worker on data that shouldn't exist.
        _write_quarantine(conn, doc_id, run_id, backend_name, model_name, _iso_now())
        return

    context = build_context(
        source_text, count_tokens=count_tokens, token_budget=context_budget
    )
    prompt = build_prompt(context)

    try:
        first = _attempt(
            conn,
            doc_id,
            run_id,
            1,
            prompt,
            source_text,
            client,
            accountant,
            config,
            count_tokens,
        )
    except _RunStopped:
        return

    if first.outcome is None:
        _write_quarantine(conn, doc_id, run_id, backend_name, model_name, _iso_now())
        return

    if not first.outcome.needs_repair:
        _write_done(
            conn,
            doc_id,
            first.outcome.fields,
            run_id,
            backend_name,
            model_name,
            _iso_now(),
        )
        return

    repair_prompt = build_repair_prompt(
        context, first.response_text or "", first.outcome.error or "invalid response"
    )
    try:
        second = _attempt(
            conn,
            doc_id,
            run_id,
            2,
            repair_prompt,
            source_text,
            client,
            accountant,
            config,
            count_tokens,
        )
    except _RunStopped:
        return

    if second.outcome is None or second.outcome.needs_repair:
        _write_quarantine(conn, doc_id, run_id, backend_name, model_name, _iso_now())
    else:
        _write_done(
            conn,
            doc_id,
            second.outcome.fields,
            run_id,
            backend_name,
            model_name,
            _iso_now(),
        )


def _attempt(
    conn: sqlite3.Connection,
    doc_id: str,
    run_id: int,
    attempt: int,
    prompt: str,
    source_text: str,
    client: LLMClient,
    accountant: _Accountant,
    config: Config,
    count_tokens: Callable[[str], int],
) -> _AttemptResult:
    """One reserve-then-call cycle. Raises `_RunStopped` (after already
    releasing `doc_id` back to `pending`) when the run-level stop condition
    fires during this attempt — budget denial or a tripped circuit breaker.
    """
    reserved_tokens = count_tokens(prompt) + config.max_output_tokens
    now = _iso_now()

    if not accountant.try_reserve(reserved_tokens):
        _release_to_pending(conn, doc_id, now)
        raise _RunStopped

    _insert_reservation(conn, run_id, doc_id, attempt, reserved_tokens, now)

    request = LLMRequest(
        prompt=prompt, json_schema=_JSON_SCHEMA, max_tokens=config.max_output_tokens
    )
    try:
        response = client.complete(request)
    except LLMBackendUnavailable:
        _release_to_pending(conn, doc_id, _iso_now())
        accountant.note_backend_unavailable()
        raise _RunStopped from None
    except LLMError:
        # Retries exhausted without tripping the breaker: a document-scoped
        # failure (decision 6) — the reservation stays, conservatively
        # unaccounted for actual usage since no response ever arrived.
        return _AttemptResult(outcome=None, response_text=None)

    _record_usage(conn, run_id, doc_id, attempt, response, _iso_now())
    outcome = validate_extraction(response.text, source_text)
    return _AttemptResult(outcome=outcome, response_text=response.text)


# --- single-document-scoped db writes (requirement 8) ----------------------


def _claim(conn: sqlite3.Connection, doc_id: str, now: str) -> bool:
    conn.execute("BEGIN IMMEDIATE")
    cur = conn.execute(
        "UPDATE documents SET status = 'in_progress', updated_at = ? "
        "WHERE id = ? AND status = 'pending'",
        (now, doc_id),
    )
    conn.execute("COMMIT")
    return cur.rowcount == 1


def _release_to_pending(conn: sqlite3.Connection, doc_id: str, now: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE documents SET status = 'pending', updated_at = ? WHERE id = ?",
        (now, doc_id),
    )
    conn.execute("COMMIT")


def _insert_reservation(
    conn: sqlite3.Connection,
    run_id: int,
    doc_id: str,
    attempt: int,
    tokens: int,
    now: str,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO token_ledger "
        "(run_id, document_id, attempt, reserved_at, reserved_tokens) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_id, doc_id, attempt, now, tokens),
    )
    conn.execute("COMMIT")


def _record_usage(
    conn: sqlite3.Connection,
    run_id: int,
    doc_id: str,
    attempt: int,
    response: LLMResponse,
    now: str,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE token_ledger SET tokens_in = ?, tokens_out = ?, completed_at = ? "
        "WHERE run_id = ? AND document_id = ? AND attempt = ?",
        (response.tokens_in, response.tokens_out, now, run_id, doc_id, attempt),
    )
    conn.execute("COMMIT")


def _write_done(
    conn: sqlite3.Connection,
    doc_id: str,
    fields: ExtractedFields,
    run_id: int,
    backend_name: str,
    model_name: str,
    now: str,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE documents SET status = 'done', quarantine_reason = NULL, "
        "doc_type = ?, counterparty_name = ?, counterparty_tax_id = ?, "
        "issue_date = ?, due_date = ?, gross_amount = ?, currency = ?, "
        "summary = ?, backend = ?, model = ?, run_id = ?, updated_at = ? "
        "WHERE id = ?",
        (
            fields.doc_type.value,
            fields.counterparty_name,
            fields.counterparty_tax_id,
            fields.issue_date.isoformat() if fields.issue_date else None,
            fields.due_date.isoformat() if fields.due_date else None,
            str(fields.gross_amount) if fields.gross_amount is not None else None,
            fields.currency,
            fields.summary,
            backend_name,
            model_name,
            run_id,
            now,
            doc_id,
        ),
    )
    conn.execute("COMMIT")


def _write_quarantine(
    conn: sqlite3.Connection,
    doc_id: str,
    run_id: int,
    backend_name: str,
    model_name: str,
    now: str,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE documents SET status = 'quarantined', "
        "quarantine_reason = 'llm_invalid_output', backend = ?, model = ?, "
        "run_id = ?, updated_at = ? WHERE id = ?",
        (backend_name, model_name, run_id, now, doc_id),
    )
    conn.execute("COMMIT")


def _reset_in_progress(conn: sqlite3.Connection, now: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE documents SET status = 'pending', updated_at = ? "
        "WHERE status = 'in_progress'",
        (now,),
    )
    conn.execute("COMMIT")


# --- run-level bookkeeping ---------------------------------------------


def _insert_run(
    conn: sqlite3.Connection,
    now: str,
    config: Config,
    backend_name: str,
    model_name: str,
    config_path: str,
    input_path: str,
    price_input_per_million: float,
    price_output_per_million: float,
) -> int:
    conn.execute("BEGIN IMMEDIATE")
    cur = conn.execute(
        "INSERT INTO runs "
        "(started_at, workers, limit_docs, budget_tokens, backend, model, "
        "config_path, input_path, price_input_per_million, "
        "price_output_per_million) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            now,
            config.workers,
            config.limit,
            config.budget,
            backend_name,
            model_name,
            config_path,
            input_path,
            price_input_per_million,
            price_output_per_million,
        ),
    )
    conn.execute("COMMIT")
    return cur.lastrowid


def _finish_run(conn: sqlite3.Connection, run_id: int, stop_reason: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE runs SET ended_at = ?, stop_reason = ? WHERE id = ?",
        (_iso_now(), stop_reason, run_id),
    )
    conn.execute("COMMIT")


def _count_attempted(conn: sqlite3.Connection) -> int:
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE status = 'done' "
        "OR (status = 'quarantined' AND quarantine_reason = 'llm_invalid_output')"
    ).fetchone()
    return count


def _sum_reserved_tokens(conn: sqlite3.Connection) -> int:
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(reserved_tokens), 0) FROM token_ledger"
    ).fetchone()
    return total


def _resolve_prices(config: Config, backend_name: str) -> tuple[float, float]:
    """`[pricing.<backend>]` from config, defaulting to 0.0 (docs/ZADANIE.md
    requirement 6: local backends cost zero, same mechanism as a paid one).
    Missing entirely (e.g. hand-built `Config.raw = {}` in tests) is treated
    the same as an explicit zero rate, not an error.
    """
    prices = config.raw.get("pricing", {}).get(backend_name, {})
    return (
        float(prices.get("input_per_million", 0.0)),
        float(prices.get("output_per_million", 0.0)),
    )


def _resolve_model_tag(config: Config) -> str:
    if config.backend == "fake":
        return "fake"
    if config.backend == "llama_server":
        cfg = config.raw.get("backend", {}).get("llama_server", {})
        return cfg.get("model_file") or cfg.get("model_repo") or "unknown"
    if config.backend == "ollama":
        cfg = config.raw.get("backend", {}).get("ollama", {})
        return cfg.get("model_tag") or "unknown"
    return config.backend


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
