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
    # LLMRequest.max_tokens and the fixed worst-case component of every
    # token_ledger reservation (Stage 7) — generous for one compact JSON
    # object with a one-sentence summary, an order of magnitude below the
    # ~1500-2000 token/doc input budget docs/PROJECT_NOTES.md §4 sets.
    # Defaulted here (not just in TOML) so existing call sites that build a
    # Config directly (tests) keep working unchanged.
    max_output_tokens: int = 300


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
        max_output_tokens=run_cfg.get("max_output_tokens", 300),
    )
