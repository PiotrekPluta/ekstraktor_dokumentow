#!/usr/bin/env sh
# POSIX sh, not bash: macOS ships bash 3.2. Keep logic in Python, not here.
set -eu

command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh

uv sync --locked
uv run python scripts/fetch_runtime.py
uv run python scripts/smoke_test_backend.py
