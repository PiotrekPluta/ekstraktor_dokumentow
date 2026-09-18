#!/usr/bin/env sh
# POSIX sh, not bash: macOS ships bash 3.2. Keep logic in Python, not here.
set -eu

command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh

# --all-groups: `tests/test_generator.py` imports `scripts/generate_data.py`
# (Pillow/reportlab, the `datagen` group) at collection time, not just
# `dev` (pytest/ruff) — a bare `uv sync --locked` leaves that import
# missing and aborts the entire suite before a single test runs. Found via
# a real fresh-clone run (GitHub Actions' macos-14 job), not caught
# earlier because every local dev venv already had `datagen` installed
# from Stage 1.
uv sync --locked --all-groups
uv run python scripts/fetch_runtime.py
uv run python scripts/smoke_test_backend.py
