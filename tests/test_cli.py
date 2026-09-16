"""CLI-level smoke tests. `run` is wired end-to-end since Stage 7 (always
exercised here against the `fake` backend, never the default
`config/default.toml` which points at `llama_server` — CLAUDE.md's "tests
never call a real model, hit the network, or need an API key" rule);
`report`/`eval` stay Stage 0 stubs until Stage 8.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from extractor.cli import app

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED = REPO_ROOT / "data" / "expected.jsonl"


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "report", "eval"):
        assert command in result.output


def _fake_backend_config(tmp_path: Path) -> Path:
    config = tmp_path / "fake.toml"
    config.write_text(
        '[run]\nworkers = 1\n\n[backend]\nname = "fake"\n', encoding="utf-8"
    )
    return config


def test_run_processes_documents_end_to_end_with_fake_backend(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.txt").write_text("Faktura nr 1, kwota 100 zl", encoding="utf-8")
    db = tmp_path / "out.sqlite"

    result = runner.invoke(
        app,
        [
            "run",
            "--input",
            str(src),
            "--db",
            str(db),
            "--config",
            str(_fake_backend_config(tmp_path)),
        ],
    )

    assert result.exit_code == 0
    assert "stop_reason=completed" in result.output


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
