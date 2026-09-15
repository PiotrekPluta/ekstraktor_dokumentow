"""Stage 4: context windowing for long documents.

`build_context` assembles a bounded string from a long document's head,
tail, and windows around keyword/candidate hits (docs/PROJECT_NOTES.md §8
Stage 4) — "regardless of where in the document" a field appears, without
sending a few-hundred-page PDF to the model.

Budget is in characters internally — window positions come from regex
hits, which are character offsets regardless — but `build_context` takes
an optional `count_tokens` (e.g. `LlamaServerClient.count_tokens`, Stage
5b) that, when given, replaces CHARS_PER_TOKEN_PROXY with a ratio measured
for real on a sample of this document, only to size the budget correctly;
the windowing algorithm itself doesn't change. No counter given (the
common case until Stage 7 wires one through — it needs a live server)
falls back to the proxy unchanged.

Operates on lightly-cleaned text (clean_for_context: NFC + whitespace tidy
only), not extractor/normalize.py's normalize_text() — that function's
case-folding and divider-stripping exist purely to make dedup-identity
hashing robust to formatting noise and would only make this text harder
for a human or model to read, for no benefit (this text is never hashed).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from extractor.candidates import find_all_candidates

# ~4 chars/token is the usual rough proxy for English; Polish's diacritics
# and richer inflection likely means somewhat more tokens per char in
# practice, so this errs on the generous side of what "fits" — acceptable
# for now since nothing downstream enforces this budget yet (Stage 5/7
# will, with real counts). Revisit once a real tokenizer is wired in.
CHARS_PER_TOKEN_PROXY = 4
# docs/PROJECT_NOTES.md §4: default model context capped ~1500-2000
# tokens/doc.
DEFAULT_TOKEN_BUDGET = 1500
DEFAULT_CHAR_BUDGET = DEFAULT_TOKEN_BUDGET * CHARS_PER_TOKEN_PROXY

HEAD_FRACTION = 0.35
TAIL_FRACTION = 0.25
WINDOW_RADIUS = 150  # chars of context kept on each side of a keyword/candidate hit

_OMISSION_MARKER = "\n[...]\n"

_KEYWORDS = (
    "NIP",
    "REGON",
    "brutto",
    "netto",
    "do zapłaty",
    "termin płatności",
    "kwota",
    "suma",
    "razem",
    "waluta",
    "total",
    "amount due",
    "due date",
    "currency",
    "gross",
)
_KEYWORD_RE = re.compile("|".join(re.escape(k) for k in _KEYWORDS), re.IGNORECASE)


def clean_for_context(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass(frozen=True)
class _Window:
    start: int
    end: int


def build_context(
    text: str,
    char_budget: int = DEFAULT_CHAR_BUDGET,
    *,
    count_tokens: Callable[[str], int] | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> str:
    """Returns `text` unchanged if it already fits the budget; otherwise a
    head + tail + budget-bounded set of windows around keyword/candidate
    hits in the middle, joined by an explicit omission marker so the model
    (and a human debugging a bad answer) can tell context was cut.

    `count_tokens`, when given, replaces the char-per-token proxy with a
    ratio measured for real on a sample of `text` — see module docstring.
    """
    text = clean_for_context(text)

    effective_char_budget = char_budget
    if count_tokens is not None:
        effective_char_budget = _measure_char_budget(text, count_tokens, token_budget)

    if len(text) <= effective_char_budget:
        return text

    head_end = int(effective_char_budget * HEAD_FRACTION)
    tail_len = int(effective_char_budget * TAIL_FRACTION)
    tail_start = max(head_end, len(text) - tail_len)
    middle_budget = effective_char_budget - head_end - (len(text) - tail_start)

    hit_positions = _find_hit_positions(text, head_end, tail_start)
    windows = _select_windows(hit_positions, head_end, tail_start, middle_budget)

    pieces = [text[:head_end]]
    pieces.extend(text[w.start : w.end] for w in windows)
    pieces.append(text[tail_start:])
    return _OMISSION_MARKER.join(p for p in pieces if p.strip())


def _measure_char_budget(
    text: str, count_tokens: Callable[[str], int], token_budget: int
) -> int:
    sample = text[: min(len(text), 4000)]
    sample_tokens = count_tokens(sample)
    if sample_tokens <= 0:
        return DEFAULT_CHAR_BUDGET  # degenerate counter response — fall back
    chars_per_token = len(sample) / sample_tokens
    return int(token_budget * chars_per_token)


def _find_hit_positions(text: str, region_start: int, region_end: int) -> list[int]:
    positions = {
        m.start() for m in _KEYWORD_RE.finditer(text, region_start, region_end)
    }
    positions.update(
        m.start() for m in find_all_candidates(text, region_start, region_end)
    )
    return sorted(positions)


def _select_windows(
    hit_positions: list[int], region_start: int, region_end: int, budget: int
) -> list[_Window]:
    windows: list[_Window] = []
    used = 0
    cursor = region_start
    for pos in hit_positions:
        if used >= budget:
            break
        start = max(pos - WINDOW_RADIUS, region_start, cursor)
        end = min(pos + WINDOW_RADIUS, region_end)
        if start >= end:
            continue
        if used + (end - start) > budget:
            end = start + (budget - used)
            if end <= start:
                break
        windows.append(_Window(start, end))
        used += end - start
        cursor = end
    return windows
