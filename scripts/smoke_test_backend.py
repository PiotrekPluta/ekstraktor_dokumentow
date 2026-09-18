"""Runs at the end of `setup.sh`, after `fetch_runtime.py`: starts the
pinned `llama-server` (via `extractor.llm.lifecycle`, the same code path
`extractor run`'s self-healing uses) and sends one real, tiny completion
through it.

This is what makes `./setup.sh` actually start "the whole environment"
rather than only fetch files for it — the server is left running on
success, not torn down, so an immediately-following `uv run extractor
run` finds it already up.

`fetch_runtime.py` only checksum-verifies the downloaded *bytes*; it can't
tell you the GGUF actually loads or that the pinned llama-server release
can serve it (wrong quantisation support, an incompatible chat template,
etc.). A real completion — not just a `/health` 200 — is the only way to
catch that at install time instead of on a reviewer's first `run`.

Skips entirely (exit 0) when the selected config doesn't select
`llama_server` — same scope as `fetch_runtime.py`, which never fetches
anything for `ollama` either (`ARCHITECTURE.md`: that backend's model/
process lifecycle stays a manual, out-of-scope step).

Reads `config/default.toml` unless `EXTRACTOR_CONFIG_PATH` is set — a
reviewer running `./setup.sh` as documented never sets it, so their
behaviour is unchanged. It exists for `.github/workflows/macos.yml`'s
manual end-to-end job: `config/ci_macos.toml` pins the exact same model as
`config/default.toml` (`fetch_runtime.py` itself is never overridable —
there is only ever one pinned model to fetch) but adds `extra_args =
["-ngl", "0"]`, since GitHub's `macos-14` runners are arm64 without
working Metal under Apple's virtualisation (`PROJECT_NOTES.md` §5) — the
same reason `lifecycle.py`'s module docstring gives for never guessing
`-ngl` from `platform.system()`.
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

from extractor.llm.client import LLMError, LLMRequest
from extractor.llm.lifecycle import (
    DEFAULT_STARTUP_TIMEOUT_S,
    VENDOR_DIR,
    ServerLifecycleError,
    ensure_running,
)
from extractor.llm.llama_server import LlamaServerClient

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "default.toml"


def _resolve_config_path() -> Path:
    override = os.environ.get("EXTRACTOR_CONFIG_PATH")
    return Path(override) if override else DEFAULT_CONFIG_PATH


def main() -> None:
    config_path = _resolve_config_path()
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    backend = config.get("backend", {}).get("name", "fake")
    if backend != "llama_server":
        print(
            f"smoke_test_backend: configured backend is {backend!r}, "
            "nothing to start, skipping"
        )
        return

    llama_cfg = config["backend"]["llama_server"]
    n_slots = config.get("run", {}).get("workers", 4)
    log_path = VENDOR_DIR / "llama-server.log"
    startup_timeout_s = llama_cfg.get("startup_timeout_s", DEFAULT_STARTUP_TIMEOUT_S)

    print(
        f"smoke_test_backend: ensuring llama-server is up on {llama_cfg['host']}:"
        f"{llama_cfg['port']} (--parallel {n_slots}, startup_timeout_s="
        f"{startup_timeout_s})..."
    )
    try:
        started = ensure_running(
            llama_cfg,
            VENDOR_DIR,
            n_slots,
            log_path,
            startup_timeout_s=startup_timeout_s,
            on_waiting=lambda elapsed: print(
                f"smoke_test_backend: still waiting for /health "
                f"({elapsed:.0f}s elapsed)..."
            ),
        )
    except ServerLifecycleError as exc:
        print(f"smoke_test_backend: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(
        "smoke_test_backend: server "
        f"{'started' if started else 'was already running'}, "
        "sending one test completion..."
    )
    client = LlamaServerClient(
        llama_cfg["host"], llama_cfg["port"], timeout_s=llama_cfg.get("timeout_s", 60.0)
    )
    try:
        response = client.complete(
            LLMRequest(prompt="Hi", json_schema={}, max_tokens=1)
        )
    except LLMError as exc:
        print(
            f"smoke_test_backend: server is up but a real completion failed: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    print(
        "smoke_test_backend: OK — model answered "
        f"({response.tokens_out} token(s) generated). Server left running on "
        f"{llama_cfg['host']}:{llama_cfg['port']} for `extractor run` to use."
    )


if __name__ == "__main__":
    main()
