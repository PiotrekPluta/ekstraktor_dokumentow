"""Stage 0 smoke tests: the CLI parses arguments and wires up config for all
three subcommands, even though none of them is implemented yet.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from extractor.cli import app

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "data" / "corpus"
EXPECTED = REPO_ROOT / "data" / "expected.jsonl"


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "report", "eval"):
        assert command in result.output


def test_run_parses_args_and_reads_config(tmp_path: Path) -> None:
    db = tmp_path / "out.sqlite"
    result = runner.invoke(
        app,
        ["run", "--input", str(CORPUS), "--db", str(db)],
    )
    assert result.exit_code == 1
    assert "not implemented yet" in result.output
    assert "backend='fake'" in result.output


def test_run_rejects_missing_input(tmp_path: Path) -> None:
    db = tmp_path / "out.sqlite"
    result = runner.invoke(
        app,
        ["run", "--input", str(tmp_path / "does-not-exist"), "--db", str(db)],
    )
    assert result.exit_code != 0


def test_report_parses_args(tmp_path: Path) -> None:
    db = tmp_path / "out.sqlite"
    result = runner.invoke(app, ["report", "--db", str(db), "--json"])
    assert result.exit_code == 1
    assert "not implemented yet" in result.output


def test_eval_parses_args(tmp_path: Path) -> None:
    db = tmp_path / "out.sqlite"
    result = runner.invoke(app, ["eval", "--db", str(db), "--expected", str(EXPECTED)])
    assert result.exit_code == 1
    assert "not implemented yet" in result.output


def test_eval_rejects_missing_expected(tmp_path: Path) -> None:
    db = tmp_path / "out.sqlite"
    result = runner.invoke(
        app,
        ["eval", "--db", str(db), "--expected", str(tmp_path / "missing.jsonl")],
    )
    assert result.exit_code != 0
