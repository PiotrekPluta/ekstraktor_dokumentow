"""scripts/smoke_test_backend.py's own logic — `ensure_running` and
`LlamaServerClient.complete` are monkeypatched out, so this never spawns a
real process or touches the network/model, per requirement 10.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from extractor.llm.client import LLMConnectionError, LLMResponse

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import smoke_test_backend


@pytest.fixture(autouse=True)
def _config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_path = tmp_path / "default.toml"
    monkeypatch.setattr(smoke_test_backend, "CONFIG_PATH", config_path)
    return config_path


def test_skips_when_backend_is_not_llama_server(
    _config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_path.write_text('[backend]\nname = "fake"\n', encoding="utf-8")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("must not touch the server for a non-llama_server backend")

    monkeypatch.setattr(smoke_test_backend, "ensure_running", fail_if_called)

    smoke_test_backend.main()  # must not raise


def test_exits_nonzero_when_server_never_becomes_healthy(
    _config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_path.write_text(
        '[backend]\nname = "llama_server"\n\n'
        '[backend.llama_server]\nhost = "127.0.0.1"\nport = 8080\n'
        "context_tokens = 2048\n",
        encoding="utf-8",
    )

    def raise_lifecycle_error(*args, **kwargs):
        raise smoke_test_backend.ServerLifecycleError("never came up")

    monkeypatch.setattr(smoke_test_backend, "ensure_running", raise_lifecycle_error)

    with pytest.raises(SystemExit):
        smoke_test_backend.main()


def test_exits_nonzero_when_completion_fails(
    _config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config_path.write_text(
        '[backend]\nname = "llama_server"\n\n'
        '[backend.llama_server]\nhost = "127.0.0.1"\nport = 8080\n'
        "context_tokens = 2048\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(smoke_test_backend, "ensure_running", lambda *a, **k: True)

    def fail_complete(self, request):
        raise LLMConnectionError("boom")

    monkeypatch.setattr(smoke_test_backend.LlamaServerClient, "complete", fail_complete)

    with pytest.raises(SystemExit):
        smoke_test_backend.main()


def test_succeeds_when_server_healthy_and_completion_works(
    _config_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _config_path.write_text(
        "[run]\nworkers = 2\n\n"
        '[backend]\nname = "llama_server"\n\n'
        '[backend.llama_server]\nhost = "127.0.0.1"\nport = 8080\n'
        "context_tokens = 2048\n",
        encoding="utf-8",
    )
    seen_n_slots = {}

    def fake_ensure_running(llama_cfg, vendor_dir, n_slots, log_path):
        seen_n_slots["value"] = n_slots
        return True

    monkeypatch.setattr(smoke_test_backend, "ensure_running", fake_ensure_running)

    def fake_complete(self, request):
        return LLMResponse(text="hi", tokens_in=1, tokens_out=1)

    monkeypatch.setattr(smoke_test_backend.LlamaServerClient, "complete", fake_complete)

    smoke_test_backend.main()  # must not raise

    assert seen_n_slots["value"] == 2
    assert "OK" in capsys.readouterr().out
