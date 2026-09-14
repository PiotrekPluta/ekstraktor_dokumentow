"""Regex candidate finders for NIP/date/amount-shaped spans.

Two consumers, both fine with imprecision in different directions:

- Stage 4 (extractor/windowing.py) uses hit *positions* as anchors for
  which parts of a long document to keep — a false positive just keeps a
  few extra characters of context around something that turns out to be a
  decoy (REGON, KRS, an invoice number all being NIP-shaped per
  docs/DATA_SPEC.md §3.1 is the point: they're deliberately indistinguishable
  by shape alone), which is harmless.
- Stage 6 (not built yet) will use these to cross-check the model's
  *values*, where precision matters more — mod-11 checksum validation,
  currency resolution precedence etc. belong there, not here.

Not exhaustive by design: Polish genitive month names and Roman-numeral
dates cover the corpus's real formats; some exotic representations of any
of these three fields could still be missed. Missing a candidate only
costs windowing an anchor point — head/tail coverage and the keyword list
mean nothing is silently invisible to the model.
"""

from __future__ import annotations

import re

_NIP_CANDIDATE_RE = re.compile(
    r"(?:NIP|VAT)?[\s:]{0,3}(?:PL[\s-]?)?\d[\d\s\-]{8,18}\d", re.IGNORECASE
)

_POLISH_MONTHS = (
    "stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|"
    "września|października|listopada|grudnia"
)
_ENGLISH_MONTHS = (
    "January|February|March|April|May|June|July|August|"
    "September|October|November|December"
)
_ROMAN_MONTHS = "I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII"

_DATE_CANDIDATE_RE = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}"  # ISO: 2024-03-12
    rf"|\d{{1,2}}\.\d{{1,2}}\.\d{{4}}"  # dotted: 12.03.2024
    rf"|\d{{1,2}}/\d{{1,2}}/\d{{4}}"  # slash: 03/12/2024
    rf"|\d{{1,2}}\s+(?:{_POLISH_MONTHS})\s+\d{{4}}"  # 12 marca 2024
    rf"|\d{{1,2}}\s+(?:{_ROMAN_MONTHS})\s+\d{{4}}"  # 12 III 2024
    rf"|(?:{_ENGLISH_MONTHS})\s+\d{{1,2}},?\s+\d{{4}}"  # March 12, 2024
    rf"|\d{{1,2}}\s+(?:{_ENGLISH_MONTHS})\s+\d{{4}}",  # 12 March 2024
    re.IGNORECASE,
)

_CURRENCY_TOKEN = r"(?:[$€]|PLN|USD|EUR|GBP|zł)"
_AMOUNT_CANDIDATE_RE = re.compile(
    rf"{_CURRENCY_TOKEN}?\s*-?\s*"
    # Thousands-grouped (1 234,56 / 1,234.56) needs >=1 group of exactly
    # three digits; a plain 4+-digit whole part with no grouping at all
    # (1234.56, a correction invoice's -450.00) is the second branch —
    # without it, \d{1,3} alone would match only a prefix of an ungrouped
    # number (e.g. just "234" out of "1234.56") and miss its lead digit(s).
    # \s already matches NBSP/narrow-NBSP thousands separators in Python's
    # str-mode re (docs/PROJECT_NOTES.md's "extremely common in real PDFs").
    r"(?:\d{1,3}(?:[\s.,]\d{3})+[.,]\d{2}"
    r"|\d+[.,]\d{2})"
    rf"\s*{_CURRENCY_TOKEN}?",
    re.IGNORECASE,
)

_NIP_DIGIT_COUNT = 10


def find_nip_candidates(
    text: str, start: int = 0, end: int | None = None
) -> list[re.Match[str]]:
    region_end = len(text) if end is None else end
    return [
        m
        for m in _NIP_CANDIDATE_RE.finditer(text, start, region_end)
        if len(re.sub(r"\D", "", m.group())) == _NIP_DIGIT_COUNT
    ]


def find_date_candidates(
    text: str, start: int = 0, end: int | None = None
) -> list[re.Match[str]]:
    region_end = len(text) if end is None else end
    return list(_DATE_CANDIDATE_RE.finditer(text, start, region_end))


def find_amount_candidates(
    text: str, start: int = 0, end: int | None = None
) -> list[re.Match[str]]:
    region_end = len(text) if end is None else end
    return list(_AMOUNT_CANDIDATE_RE.finditer(text, start, region_end))


def find_all_candidates(
    text: str, start: int = 0, end: int | None = None
) -> list[re.Match[str]]:
    return [
        *find_nip_candidates(text, start, end),
        *find_date_candidates(text, start, end),
        *find_amount_candidates(text, start, end),
    ]
