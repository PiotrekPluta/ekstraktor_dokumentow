"""Stage 4: context windowing for long documents.

`build_context` assembles a bounded string from a long document's head,
tail, and windows around keyword/candidate hits (docs/PROJECT_NOTES.md §8
Stage 4) — "regardless of where in the document" a field appears, without
sending a few-hundred-page PDF to the model.

Budget is in characters, not tokens: no model is pinned yet (Stage 5), so
there is no real tokenizer to count against. CHARS_PER_TOKEN_PROXY is a
rough, deliberately generous stand-in — Stage 5 replaces char_budget with
real tokenizer-based counting (offline via tokenizer.json or the backend's
/tokenize, per the plan) rather than refining this proxy further.

Operates on lightly-cleaned text (clean_for_context: NFC + whitespace tidy
only), not extractor/normalize.py's normalize_text() — that function's
case-folding and divider-stripping exist purely to make dedup-identity
hashing robust to formatting noise and would only make this text harder
for a human or model to read, for no benefit (this text is never hashed).
"""

from __future__ import annotations

import re
import unicodedata
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


def build_context(text: str, char_budget: int = DEFAULT_CHAR_BUDGET) -> str:
    """Returns `text` unchanged if it already fits `char_budget`; otherwise
    a head + tail + budget-bounded set of windows around keyword/candidate
    hits in the middle, joined by an explicit omission marker so the model
    (and a human debugging a bad answer) can tell context was cut.
    """
    text = clean_for_context(text)
    if len(text) <= char_budget:
        return text

    head_end = int(char_budget * HEAD_FRACTION)
    tail_len = int(char_budget * TAIL_FRACTION)
    tail_start = max(head_end, len(text) - tail_len)
    middle_budget = char_budget - head_end - (len(text) - tail_start)

    hit_positions = _find_hit_positions(text, head_end, tail_start)
    windows = _select_windows(hit_positions, head_end, tail_start, middle_budget)

    pieces = [text[:head_end]]
    pieces.extend(text[w.start : w.end] for w in windows)
    pieces.append(text[tail_start:])
    return _OMISSION_MARKER.join(p for p in pieces if p.strip())


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
