"""Loading of the TOML run configuration (backend choice, budgets, pricing).

See docs/PROJECT_NOTES.md §4 and §8 (Stage 7) for how these values get used
once orchestration exists. For now this only parses and exposes them.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Config:
    workers: int
    limit: int | None
    budget: int | None
    backend: str
    raw: dict[str, Any]


def load_config(path: Path) -> Config:
    with path.open("rb") as fh:
        data = tomllib.load(fh)

    run_cfg = data.get("run", {})
    backend_cfg = data.get("backend", {})

    return Config(
        workers=run_cfg.get("workers", 4),
        limit=run_cfg.get("limit") or None,
        budget=run_cfg.get("budget") or None,
        backend=backend_cfg.get("name", "fake"),
        raw=data,
    )
