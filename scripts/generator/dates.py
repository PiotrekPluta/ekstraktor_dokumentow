"""Date formatting variants (DATA_SPEC.md §3.3)."""

from __future__ import annotations

from datetime import date, timedelta

POLISH_GENITIVE_MONTHS = [
    "stycznia",
    "lutego",
    "marca",
    "kwietnia",
    "maja",
    "czerwca",
    "lipca",
    "sierpnia",
    "września",
    "października",
    "listopada",
    "grudnia",
]

ROMAN_MONTHS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]

ENGLISH_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def iso(d: date) -> str:
    return d.isoformat()


def dotted(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def polish_long(d: date) -> str:
    return f"{d.day} {POLISH_GENITIVE_MONTHS[d.month - 1]} {d.year}"


def polish_roman(d: date) -> str:
    return f"{d.day} {ROMAN_MONTHS[d.month - 1]} {d.year}"


def english_long(d: date) -> str:
    return f"{ENGLISH_MONTHS[d.month - 1]} {d.day}, {d.year}"


def us_slash_ambiguous(d: date) -> str:
    """MM/DD/YYYY — genuinely ambiguous with DD/MM/YYYY; only used on English
    documents so the US reading is the defensible one (DATA_SPEC.md §3.3)."""
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def derive_due_date(issue: date, days: int) -> date:
    """"termin płatności: N dni od daty wystawienia" (DATA_SPEC.md §3.3)."""
    return issue + timedelta(days=days)
