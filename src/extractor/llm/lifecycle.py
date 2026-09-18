"""Starts/health-checks the local `llama-server` process — the "Stage
7/CLI job" `llama_server.LlamaServerClient`'s own docstring explicitly
puts outside that module's scope (it only ever talks to a server already
listening on `host:port`).

Two callers, both deliberately going through the same `ensure_running()`:

- `extractor.cli.run` (self-healing): before every run, check `/health`
  and start the server if it isn't up. Makes `run` work whether or not
  `setup.sh`'s smoke test (below) is what started it, and survives the
  server dying or the machine rebooting between runs — the same
  resume-friendly posture Stage 7 already has for the orchestration loop
  itself (docs/PROJECT_NOTES.md §8 Stage 7).
- `scripts/smoke_test_backend.py` (setup.sh): start it once at install
  time and leave it running — this is what makes `./setup.sh` actually
  start "the whole environment" rather than only fetching files for it.

Deliberately scoped to the `llama_server` backend only, matching
`fetch_runtime.py`'s own scope (`ARCHITECTURE.md`: Ollama's model/process
lifecycle stays a manual, out-of-scope step — `fetch_runtime.py` never
runs `ollama pull` either).

`--ctx-size` is computed here as `context_tokens * n_slots`, not read as a
flat number — the shared-KV-cache-across-slots bug this project already
hit and documented (`ARCHITECTURE.md`: four individually-fine requests
still failed once `llama-server`'s parallel slots split one flat
`--ctx-size` between them). Since this module is now what actually starts
the process, `--parallel` is always set to the same `n_slots` used for
`--ctx-size`'s multiplication — the two can no longer drift apart the way
`ARCHITECTURE.md` flagged as an unowned risk when a human started the
server by hand.

`-ngl`/`-t` are deliberately not guessed from `platform.system()`: an
arm64 `Darwin` host is not reliably a GPU-capable one — `PROJECT_NOTES.md`
§5 notes GitHub Actions' own `macos-14` runners are arm64 but Metal
Performance Shaders don't work under Apple's virtualisation there, so the
project's own CI smoke test needs `-ngl 0` on the same OS/arch combination
that a real M1 Mac needs Metal enabled on. `extra_args` (optional, under
`[backend.llama_server]`) is the escape hatch for whichever flags a given
environment actually needs, instead of a guess baked into this code that
would be wrong for one of the two.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from extractor.config import Config

REPO_ROOT = Path(__file__).resolve().parents[3]
VENDOR_DIR = REPO_ROOT / "vendor"

DEFAULT_STARTUP_TIMEOUT_S = 60.0
DEFAULT_POLL_INTERVAL_S = 0.5
_HEALTH_CHECK_TIMEOUT_S = 2.0


class ServerLifecycleError(RuntimeError):
    """The server couldn't be confirmed healthy — either
    `vendor/versions.lock` is missing (`setup.sh` was never run) or the
    process didn't answer `/health` within the startup timeout.
    """


def is_healthy(
    host: str, port: int, *, transport: httpx.BaseTransport | None = None
) -> bool:
    try:
        with httpx.Client(
            transport=transport, timeout=_HEALTH_CHECK_TIMEOUT_S
        ) as client:
            response = client.get(f"http://{host}:{port}/health")
            return response.status_code == 200
    except httpx.HTTPError:
        return False


def read_versions_lock(vendor_dir: Path) -> dict:
    lock_path = vendor_dir / "versions.lock"
    if not lock_path.exists():
        raise ServerLifecycleError(
            f"{lock_path} not found — run ./setup.sh first to fetch the "
            "pinned model and llama-server binary."
        )
    return json.loads(lock_path.read_text(encoding="utf-8"))


def build_argv(llama_cfg: dict, versions: dict, n_slots: int) -> list[str]:
    ctx_size = llama_cfg["context_tokens"] * n_slots
    argv = [
        versions["llama_server_path"],
        "-m",
        versions["model_path"],
        "--host",
        llama_cfg["host"],
        "--port",
        str(llama_cfg["port"]),
        "--ctx-size",
        str(ctx_size),
        "--parallel",
        str(n_slots),
    ]
    argv.extend(str(arg) for arg in llama_cfg.get("extra_args", []))
    return argv


def start_process(argv: list[str], log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("ab")
    # start_new_session detaches the child from this process's session (the
    # `nohup ... & disown` two-step a human would otherwise run by hand) so
    # the server outlives a `run`/smoke-test invocation that started it.
    return subprocess.Popen(
        argv,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


_LOG_TAIL_LINES = 40


def _log_tail(log_path: Path | None) -> str:
    """Read for a timeout error message — `start_process()` redirects the
    server's own stdout/stderr straight to `log_path`, never to whatever
    process called `ensure_running()`. Without this, a CI step (or a
    `run` invocation) that never captures that file separately gets no
    information beyond "didn't become healthy" — found missing for real
    against `macos-smoke-e2e.yml`'s first genuine timeout, where the
    workflow's own visible output had nothing else to go on.
    """
    if log_path is None or not log_path.exists():
        return ""
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = "\n".join(lines[-_LOG_TAIL_LINES:])
    return f"\n\n--- last {min(len(lines), _LOG_TAIL_LINES)} line(s) of {log_path} ---\n{tail}"


def wait_until_healthy(
    host: str,
    port: int,
    *,
    timeout_s: float,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log_path: Path | None = None,
) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        if is_healthy(host, port, transport=transport):
            return
        if time.monotonic() >= deadline:
            raise ServerLifecycleError(
                f"llama-server did not become healthy within {timeout_s}s "
                f"(http://{host}:{port}/health) — check its log."
                f"{_log_tail(log_path)}"
            )
        sleep(poll_interval_s)


def ensure_running(
    llama_cfg: dict,
    vendor_dir: Path,
    n_slots: int,
    log_path: Path,
    *,
    startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    start_fn: Callable[[list[str], Path], subprocess.Popen] = start_process,
) -> bool:
    """Idempotent: a `/health` 200 means some server (started by us before,
    by `setup.sh`'s smoke test, or by hand) is already answering on this
    host:port, and is left alone — this is what lets a manually-started
    server (e.g. with hand-picked `-ngl`/`-t` for a specific machine) take
    precedence without needing an opt-out flag.

    Returns True if this call started a new process, False if one was
    already healthy.
    """
    host, port = llama_cfg["host"], llama_cfg["port"]
    if is_healthy(host, port, transport=transport):
        return False

    versions = read_versions_lock(vendor_dir)
    argv = build_argv(llama_cfg, versions, n_slots)
    start_fn(argv, log_path)
    wait_until_healthy(
        host,
        port,
        timeout_s=startup_timeout_s,
        poll_interval_s=poll_interval_s,
        transport=transport,
        sleep=sleep,
        log_path=log_path,
    )
    return True


def ensure_backend_ready(config: Config, *, vendor_dir: Path = VENDOR_DIR) -> bool:
    """`extractor.cli.run`'s self-healing entry point. No-op (returns
    False) for `fake` (nothing to start) and `ollama` (lifecycle
    management for it is out of scope — see module docstring).
    """
    if config.backend != "llama_server":
        return False
    llama_cfg = config.raw["backend"]["llama_server"]
    return ensure_running(
        llama_cfg, vendor_dir, config.workers, vendor_dir / "llama-server.log"
    )
