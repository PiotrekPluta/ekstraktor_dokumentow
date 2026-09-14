"""CLI entry point: `run`, `report`, `eval` — see docs/ZADANIE.md §3 for the
exact interface this must satisfy, and docs/PROJECT_NOTES.md §8 for the
staged implementation plan each command belongs to.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from extractor.config import load_config

app = typer.Typer(add_completion=False, no_args_is_help=True)

InputOpt = Annotated[
    Path, typer.Option("--input", exists=True, help="Input directory or zip archive.")
]
DbOpt = Annotated[
    Path, typer.Option("--db", help="Path to the output SQLite database.")
]
WorkersOpt = Annotated[
    int, typer.Option("--workers", min=1, help="Parallel processing units.")
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
    workers: WorkersOpt = 4,
    limit: LimitOpt = None,
    budget: BudgetOpt = None,
    config: ConfigOpt = Path("config/default.toml"),
) -> None:
    """Process INPUT into DB, resuming any previous run found there."""
    cfg = load_config(config)
    _not_implemented(
        "run",
        f"would use backend={cfg.backend!r}, workers={workers}, "
        f"limit={limit}, budget={budget}, input={input_path}, db={db}",
    )


@app.command()
def report(db: DbOpt, as_json: AsJsonOpt = False) -> None:
    """Print a report summarising the contents of DB (see docs/ZADANIE.md §7)."""
    _not_implemented("report", f"would read db={db}, json={as_json}")


@app.command(name="eval")
def eval_(db: DbOpt, expected: ExpectedOpt) -> None:
    """Score DB against EXPECTED per field (see docs/ZADANIE.md §3)."""
    _not_implemented("eval", f"would compare db={db} against expected={expected}")


def _not_implemented(command: str, detail: str) -> None:
    typer.echo(
        f"'{command}' is not implemented yet (Stage 0 skeleton). {detail}", err=True
    )
    raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
