"""Offline, real token counting via a committed `tokenizer.json`
(`assets/tokenizer.json`) — Bielik-4.5B-v3.0-Instruct's tokenizer.

Checked into the repo directly rather than fetched: the base (non-GGUF)
Hugging Face repo that ships this file is gated, so
`scripts/fetch_runtime.py` has no way to download it for a reviewer
running `setup.sh` fresh. At ~3.7MB this is small enough to commit
outright, unlike the model itself (`vendor/`, gitignored).

This is the "offline via tokenizer.json" option docs/PROJECT_NOTES.md §8
Stage 4 names. `LlamaServerClient.count_tokens()`'s `/tokenize` call is
the other one — still available, and the only option for anyone who
strips `assets/` out of their own fork or swaps in a different model.
"""

from __future__ import annotations

from pathlib import Path

from tokenizers import Tokenizer

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TOKENIZER_PATH = REPO_ROOT / "assets" / "tokenizer.json"


def load_tokenizer(path: Path = DEFAULT_TOKENIZER_PATH) -> Tokenizer:
    return Tokenizer.from_file(str(path))


def count_tokens(tokenizer: Tokenizer, text: str) -> int:
    return len(tokenizer.encode(text).ids)
