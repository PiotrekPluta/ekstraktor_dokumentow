"""CLI entry point: `run`, `report`, `eval` — see docs/ZADANIE.md §3 for the
exact interface this must satisfy, and docs/PROJECT_NOTES.md §8 for the
staged implementation plan each command belongs to.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer

from extractor.config import load_config
from extractor.db import connect as connect_db
from extractor.eval import evaluate, format_eval_text, load_expected
from extractor.inventory import build_inventory
from extractor.llm import build_client
from extractor.orchestrate import run as orchestrate_run
from extractor.report import compute_report, format_report_text

app = typer.Typer(add_completion=False, no_args_is_help=True)

InputOpt = Annotated[
    Path, typer.Option("--input", exists=True, help="Input directory or zip archive.")
]
DbOpt = Annotated[
    Path, typer.Option("--db", help="Path to the output SQLite database.")
]
WorkersOpt = Annotated[
    int | None,
    typer.Option(
        "--workers", min=1, help="Parallel processing units. Defaults to [run].workers."
    ),
]
LimitOpt = Annotated[
    int | None, typer.Option("--limit", min=1, help="Process at most N documents.")
]
BudgetOpt = Annotated[
    int | None,
    typer.Option(
        "--budget", min=1, help="Total token budget (input + output) for this run."
    ),
]
ConfigOpt = Annotated[
    Path, typer.Option("--config", exists=True, help="Path to the TOML config file.")
]
AsJsonOpt = Annotated[bool, typer.Option("--json", help="Emit the report as JSON.")]
ExpectedOpt = Annotated[
    Path, typer.Option("--expected", exists=True, help="Path to expected.jsonl.")
]


@app.command()
def run(
    input_path: InputOpt,
    db: DbOpt,
    workers: WorkersOpt = None,
    limit: LimitOpt = None,
    budget: BudgetOpt = None,
    config: ConfigOpt = Path("config/default.toml"),
) -> None:
    """Process INPUT into DB, resuming any previous run found there.

    `--workers`/`--limit`/`--budget` override the config file's `[run]`
    defaults when given; `--limit`/`--budget` are cumulative across every
    `run` invocation against this `db` (docs/plan/Stage_7_plan.md decision
    2), not reset per call — required for "the record set after any number
    of interruptions matches an uninterrupted run" (requirement 4) to hold
    when the tool is resumed with the same `--limit`/`--budget` repeatedly.
    """
    cfg = load_config(config)
    effective = replace(
        cfg,
        workers=workers if workers is not None else cfg.workers,
        limit=limit if limit is not None else cfg.limit,
        budget=budget if budget is not None else cfg.budget,
    )

    conn = connect_db(db)
    try:
        build_inventory(conn, input_path)
    finally:
        conn.close()

    client = build_client(effective)
    result = orchestrate_run(db, input_path, client, effective, config_path=str(config))
    typer.echo(f"run_id={result.run_id} stop_reason={result.stop_reason}")


@app.command()
def report(db: DbOpt, as_json: AsJsonOpt = False) -> None:
    """Print a report summarising the contents of DB (see docs/ZADANIE.md §7)."""
    conn = connect_db(db)
    try:
        data = compute_report(conn)
    finally:
        conn.close()

    if as_json:
        typer.echo(json.dumps(data.as_dict(), ensure_ascii=False))
    else:
        typer.echo(format_report_text(data))


@app.command(name="eval")
def eval_(db: DbOpt, expected: ExpectedOpt) -> None:
    """Score DB against EXPECTED per field (see docs/ZADANIE.md §3)."""
    records = load_expected(expected)
    conn = connect_db(db)
    try:
        result = evaluate(conn, records)
    finally:
        conn.close()

    typer.echo(format_eval_text(result))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
