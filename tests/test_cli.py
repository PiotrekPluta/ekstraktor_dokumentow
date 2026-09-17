"""CLI-level smoke tests. `run`, `report` and `eval` are all wired
end-to-end (always exercised here against the `fake` backend, never the
default `config/default.toml` which points at `llama_server` — CLAUDE.md's
"tests never call a real model, hit the network, or need an API key" rule).
Deeper coverage of `report`'s balance/cost/wall-time logic and `eval`'s
per-field scoring lives in `test_report.py`/`test_eval.py`; this file only
checks the commands are wired up correctly end to end from argv to output.
"""

from __future__ import annotations

import json
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


def test_report_json_on_empty_db_balances(tmp_path: Path) -> None:
    db = tmp_path / "out.sqlite"
    result = runner.invoke(app, ["report", "--db", str(db), "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["input_files"] == 0
    assert data["input_files"] == data["duplicate_files"] + data["unique_documents"]
    assert data["unique_documents"] == (
        data["processed_ok"] + data["quarantined"] + data["not_started"]
    )
    assert data["stop_reason"] is None


def test_report_text_after_a_real_run(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.txt").write_text("Faktura nr 1, kwota 100 zl", encoding="utf-8")
    db = tmp_path / "out.sqlite"
    runner.invoke(
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

    result = runner.invoke(app, ["report", "--db", str(db)])

    assert result.exit_code == 0
    assert "unique_documents  = 1" in result.output
    assert "backend           = fake" in result.output


def test_eval_scores_a_real_run_against_expected(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.txt").write_text("Faktura nr 1, kwota 100 zl", encoding="utf-8")
    db = tmp_path / "out.sqlite"
    runner.invoke(
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
    expected = tmp_path / "expected.jsonl"
    expected.write_text(
        json.dumps(
            {
                "doc_type": "invoice",
                "counterparty_name": None,
                "counterparty_tax_id": None,
                "issue_date": None,
                "due_date": None,
                "gross_amount": None,
                "currency": None,
                "summary": "x",
                "files": ["a.txt"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["eval", "--db", str(db), "--expected", str(expected)])

    assert result.exit_code == 0
    assert "documents_expected = 1" in result.output
    assert "not_found_in_db    = 0" in result.output


def test_eval_rejects_missing_expected(tmp_path: Path) -> None:
    db = tmp_path / "out.sqlite"
    result = runner.invoke(
        app,
        ["eval", "--db", str(db), "--expected", str(tmp_path / "missing.jsonl")],
    )
    assert result.exit_code != 0
