"""extractor.llm.lifecycle against httpx.MockTransport and injected
start/sleep callables — no real process is ever spawned and no real
network call is ever made, per requirement 10.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from extractor.config import Config
from extractor.llm.lifecycle import (
    DEFAULT_STARTUP_TIMEOUT_S,
    ServerLifecycleError,
    build_argv,
    ensure_backend_ready,
    ensure_running,
    is_healthy,
    read_versions_lock,
    wait_until_healthy,
)

LLAMA_CFG = {
    "host": "127.0.0.1",
    "port": 8080,
    "context_tokens": 2048,
}


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# --- is_healthy --------------------------------------------------------


def test_is_healthy_true_on_200() -> None:
    transport = _transport(lambda request: httpx.Response(200, json={"status": "ok"}))
    assert is_healthy("127.0.0.1", 8080, transport=transport) is True


def test_is_healthy_false_on_non_200() -> None:
    transport = _transport(lambda request: httpx.Response(503, text="loading"))
    assert is_healthy("127.0.0.1", 8080, transport=transport) is False


def test_is_healthy_false_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert is_healthy("127.0.0.1", 8080, transport=_transport(handler)) is False


# --- read_versions_lock --------------------------------------------------


def test_read_versions_lock_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ServerLifecycleError, match="setup.sh"):
        read_versions_lock(tmp_path)


def test_read_versions_lock_returns_parsed_json(tmp_path: Path) -> None:
    (tmp_path / "versions.lock").write_text('{"model_path": "x"}', encoding="utf-8")
    assert read_versions_lock(tmp_path) == {"model_path": "x"}


# --- build_argv ----------------------------------------------------------


def test_build_argv_scales_ctx_size_by_n_slots() -> None:
    versions = {"llama_server_path": "/bin/llama-server", "model_path": "/m.gguf"}
    argv = build_argv(LLAMA_CFG, versions, n_slots=4)

    assert argv[0] == "/bin/llama-server"
    assert "--ctx-size" in argv
    assert argv[argv.index("--ctx-size") + 1] == str(2048 * 4)
    assert argv[argv.index("--parallel") + 1] == "4"
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--port") + 1] == "8080"
    assert argv[argv.index("-m") + 1] == "/m.gguf"


def test_build_argv_appends_extra_args() -> None:
    versions = {"llama_server_path": "/bin/llama-server", "model_path": "/m.gguf"}
    cfg = {**LLAMA_CFG, "extra_args": ["-ngl", "0"]}

    argv = build_argv(cfg, versions, n_slots=1)

    assert argv[-2:] == ["-ngl", "0"]


def test_build_argv_omits_extra_args_when_absent() -> None:
    versions = {"llama_server_path": "/bin/llama-server", "model_path": "/m.gguf"}
    argv = build_argv(LLAMA_CFG, versions, n_slots=1)
    assert "-ngl" not in argv


# --- wait_until_healthy ----------------------------------------------------


def test_wait_until_healthy_returns_once_transport_reports_ok() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="loading")
        return httpx.Response(200, json={"status": "ok"})

    wait_until_healthy(
        "127.0.0.1",
        8080,
        timeout_s=5.0,
        poll_interval_s=0.0,
        transport=_transport(handler),
        sleep=lambda _s: None,
    )
    assert calls["n"] == 3


def test_wait_until_healthy_raises_after_timeout() -> None:
    transport = _transport(lambda request: httpx.Response(503, text="loading"))

    with pytest.raises(ServerLifecycleError, match="did not become healthy"):
        wait_until_healthy(
            "127.0.0.1",
            8080,
            timeout_s=0.0,
            poll_interval_s=0.0,
            transport=transport,
            sleep=lambda _s: None,
        )


def test_wait_until_healthy_timeout_includes_log_tail(tmp_path: Path) -> None:
    log_path = tmp_path / "llama-server.log"
    log_path.write_text("line one\nline two\nfatal: something went wrong\n")
    transport = _transport(lambda request: httpx.Response(503, text="loading"))

    with pytest.raises(ServerLifecycleError, match="fatal: something went wrong"):
        wait_until_healthy(
            "127.0.0.1",
            8080,
            timeout_s=0.0,
            poll_interval_s=0.0,
            transport=transport,
            sleep=lambda _s: None,
            log_path=log_path,
        )


def test_wait_until_healthy_calls_on_waiting_periodically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fake, controllable clock rather than real sleeping: each failed
    health check "costs" 5s of fake elapsed time, so with a 10s
    progress_interval_s, on_waiting should fire roughly every other call —
    deterministic regardless of how fast this machine actually runs it.
    """
    from extractor.llm import lifecycle

    clock = {"t": 0.0}
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: clock["t"])

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        clock["t"] += 5.0
        if calls["n"] < 6:
            return httpx.Response(503, text="loading")
        return httpx.Response(200, json={"status": "ok"})

    reports = []
    wait_until_healthy(
        "127.0.0.1",
        8080,
        timeout_s=100.0,
        poll_interval_s=0.0,
        transport=_transport(handler),
        sleep=lambda _s: None,
        on_waiting=reports.append,
        progress_interval_s=10.0,
    )

    assert len(reports) >= 1
    assert all(r > 0 for r in reports)


def test_wait_until_healthy_omits_progress_reports_when_on_waiting_is_none() -> None:
    # No on_waiting given: must not raise just because there's nothing to
    # call — the default, quiet path (extractor.cli.run's self-healing).
    transport = _transport(lambda request: httpx.Response(200, json={"status": "ok"}))
    wait_until_healthy(
        "127.0.0.1", 8080, timeout_s=5.0, poll_interval_s=0.0, transport=transport
    )


def test_wait_until_healthy_timeout_without_log_file_omits_tail(tmp_path: Path) -> None:
    transport = _transport(lambda request: httpx.Response(503, text="loading"))

    with pytest.raises(ServerLifecycleError) as exc_info:
        wait_until_healthy(
            "127.0.0.1",
            8080,
            timeout_s=0.0,
            poll_interval_s=0.0,
            transport=transport,
            sleep=lambda _s: None,
            log_path=tmp_path / "missing.log",
        )
    assert "---" not in str(exc_info.value)


# --- ensure_running --------------------------------------------------------


def test_ensure_running_is_a_noop_when_already_healthy(tmp_path: Path) -> None:
    transport = _transport(lambda request: httpx.Response(200, json={"status": "ok"}))

    def start_fn_should_not_be_called(argv, log_path):
        raise AssertionError("start_fn must not run when already healthy")

    started = ensure_running(
        LLAMA_CFG,
        tmp_path,
        n_slots=2,
        log_path=tmp_path / "log",
        transport=transport,
        start_fn=start_fn_should_not_be_called,
    )
    assert started is False


def test_ensure_running_starts_process_and_waits_when_not_healthy(
    tmp_path: Path,
) -> None:
    (tmp_path / "versions.lock").write_text(
        '{"llama_server_path": "/bin/llama-server", "model_path": "/m.gguf"}',
        encoding="utf-8",
    )
    calls = {"n": 0, "start_argv": None}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503, text="loading")
        return httpx.Response(200, json={"status": "ok"})

    def fake_start(argv, log_path):
        calls["start_argv"] = argv

    started = ensure_running(
        LLAMA_CFG,
        tmp_path,
        n_slots=3,
        log_path=tmp_path / "log",
        transport=_transport(handler),
        sleep=lambda _s: None,
        start_fn=fake_start,
    )

    assert started is True
    assert calls["start_argv"][0] == "/bin/llama-server"
    assert calls["start_argv"][calls["start_argv"].index("--parallel") + 1] == "3"


def test_ensure_running_raises_when_versions_lock_missing(tmp_path: Path) -> None:
    transport = _transport(lambda request: httpx.Response(503, text="loading"))

    with pytest.raises(ServerLifecycleError, match="setup.sh"):
        ensure_running(
            LLAMA_CFG,
            tmp_path,
            n_slots=1,
            log_path=tmp_path / "log",
            transport=transport,
            start_fn=lambda argv, log_path: None,
        )


# --- ensure_backend_ready --------------------------------------------------


def _config(backend: str, workers: int = 4) -> Config:
    return Config(
        workers=workers,
        limit=None,
        budget=None,
        backend=backend,
        raw={"backend": {"llama_server": LLAMA_CFG}},
    )


def test_ensure_backend_ready_is_noop_for_fake() -> None:
    assert ensure_backend_ready(_config("fake")) is False


def test_ensure_backend_ready_is_noop_for_ollama() -> None:
    assert ensure_backend_ready(_config("ollama")) is False


def test_ensure_backend_ready_delegates_to_ensure_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from extractor.llm import lifecycle

    seen = {}

    def fake_ensure_running(llama_cfg, vendor_dir, n_slots, log_path, **kwargs):
        seen["llama_cfg"] = llama_cfg
        seen["vendor_dir"] = vendor_dir
        seen["n_slots"] = n_slots
        seen["log_path"] = log_path
        return True

    monkeypatch.setattr(lifecycle, "ensure_running", fake_ensure_running)

    result = ensure_backend_ready(
        _config("llama_server", workers=7), vendor_dir=tmp_path
    )

    assert result is True
    assert seen["llama_cfg"] == LLAMA_CFG
    assert seen["vendor_dir"] == tmp_path
    assert seen["n_slots"] == 7
    assert seen["log_path"] == tmp_path / "llama-server.log"


def test_ensure_backend_ready_uses_default_startup_timeout_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from extractor.llm import lifecycle

    seen = {}
    monkeypatch.setattr(
        lifecycle,
        "ensure_running",
        lambda *a, **kwargs: seen.update(kwargs) or True,
    )

    ensure_backend_ready(_config("llama_server"), vendor_dir=tmp_path)

    assert seen["startup_timeout_s"] == DEFAULT_STARTUP_TIMEOUT_S


def test_ensure_backend_ready_honours_configured_startup_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from extractor.llm import lifecycle

    seen = {}
    monkeypatch.setattr(
        lifecycle,
        "ensure_running",
        lambda *a, **kwargs: seen.update(kwargs) or True,
    )
    cfg = _config("llama_server")
    cfg.raw["backend"]["llama_server"] = {**LLAMA_CFG, "startup_timeout_s": 600.0}

    ensure_backend_ready(cfg, vendor_dir=tmp_path)

    assert seen["startup_timeout_s"] == 600.0
