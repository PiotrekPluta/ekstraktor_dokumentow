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

Skips entirely (exit 0) when `config/default.toml` doesn't select
`llama_server` — same scope as `fetch_runtime.py`, which never fetches
anything for `ollama` either (`ARCHITECTURE.md`: that backend's model/
process lifecycle stays a manual, out-of-scope step).
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from extractor.llm.client import LLMError, LLMRequest
from extractor.llm.lifecycle import VENDOR_DIR, ServerLifecycleError, ensure_running
from extractor.llm.llama_server import LlamaServerClient

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "default.toml"


def main() -> None:
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
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

    print(
        f"smoke_test_backend: ensuring llama-server is up on {llama_cfg['host']}:"
        f"{llama_cfg['port']} (--parallel {n_slots})..."
    )
    try:
        started = ensure_running(llama_cfg, VENDOR_DIR, n_slots, log_path)
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
