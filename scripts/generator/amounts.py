"""Amount formatting variants and Polish amount-in-words (DATA_SPEC.md §3.2)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

NBSP = " "
NARROW_NBSP = " "


def quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _split(value: Decimal) -> tuple[bool, str, str]:
    q = quantize(value)
    negative = q < 0
    q = abs(q)
    s = f"{q:.2f}"
    int_part, frac_part = s.split(".")
    return negative, int_part, frac_part


def _group(int_part: str, sep: str) -> str:
    if not sep:
        return int_part
    parts = []
    s = int_part
    while len(s) > 3:
        parts.insert(0, s[-3:])
        s = s[:-3]
    parts.insert(0, s)
    return sep.join(parts)


def format_pl_space_zl(value: Decimal) -> str:
    """``1 234,56 zł``"""
    neg, i, f = _split(value)
    sign = "-" if neg else ""
    return f"{sign}{_group(i, ' ')},{f} zł"


def format_pl_dot_pln(value: Decimal) -> str:
    """``1.234,56 PLN``"""
    neg, i, f = _split(value)
    sign = "-" if neg else ""
    return f"{sign}{_group(i, '.')},{f} PLN"


def format_en_comma(value: Decimal, currency: str) -> str:
    """``1,234.56 EUR``"""
    neg, i, f = _split(value)
    sign = "-" if neg else ""
    return f"{sign}{_group(i, ',')}.{f} {currency}"


def format_symbol_prefix_eur(value: Decimal) -> str:
    """``€ 1 234,56``"""
    neg, i, f = _split(value)
    sign = "-" if neg else ""
    return f"{sign}€ {_group(i, ' ')},{f}"


def format_nbsp(value: Decimal, unit: str) -> str:
    neg, i, f = _split(value)
    sign = "-" if neg else ""
    return f"{sign}{_group(i, NBSP)},{f}{NBSP}{unit}"


def format_narrow_nbsp(value: Decimal, unit: str) -> str:
    neg, i, f = _split(value)
    sign = "-" if neg else ""
    return f"{sign}{_group(i, NARROW_NBSP)},{f}{NARROW_NBSP}{unit}"


def format_code_prefix(value: Decimal, code: str) -> str:
    """``PLN 1234.56`` — plain, no thousands grouping."""
    neg, i, f = _split(value)
    sign = "-" if neg else ""
    return f"{code} {sign}{i}.{f}"


def format_dollar(value: Decimal) -> str:
    neg, i, f = _split(value)
    sign = "-" if neg else ""
    return f"{sign}${_group(i, ',')}.{f}"


# --- Polish amount-in-words --------------------------------------------------

_UNITS = ["", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć"]
_TEENS = [
    "dziesięć",
    "jedenaście",
    "dwanaście",
    "trzynaście",
    "czternaście",
    "piętnaście",
    "szesnaście",
    "siedemnaście",
    "osiemnaście",
    "dziewiętnaście",
]
_TENS = ["", "", "dwadzieścia", "trzydzieści", "czterdzieści", "pięćdziesiąt", "sześćdziesiąt", "siedemdziesiąt", "osiemdziesiąt", "dziewięćdziesiąt"]
_HUNDREDS = ["", "sto", "dwieście", "trzysta", "czterysta", "pięćset", "sześćset", "siedemset", "osiemset", "dziewięćset"]


def _plural_form(n: int, singular: str, few: str, many: str) -> str:
    if n == 1:
        return singular
    last_two = n % 100
    last_one = n % 10
    if 12 <= last_two <= 14:
        return many
    if 2 <= last_one <= 4:
        return few
    return many


def _block_words(n: int) -> str:
    if n == 0:
        return ""
    words = []
    h, rem = divmod(n, 100)
    if h:
        words.append(_HUNDREDS[h])
    if 10 <= rem <= 19:
        words.append(_TEENS[rem - 10])
    else:
        t, u = divmod(rem, 10)
        if t:
            words.append(_TENS[t])
        if u:
            words.append(_UNITS[u])
    return " ".join(words)


def _scale_words(n: int, singular: str, few: str, many: str) -> str:
    if n == 1:
        return singular
    word = _plural_form(n, singular, few, many)
    return f"{_block_words(n)} {word}"


def integer_to_polish_words(n: int) -> str:
    if n == 0:
        return "zero"
    parts = []
    millions, rem = divmod(n, 1_000_000)
    thousands, rest = divmod(rem, 1000)
    if millions:
        parts.append(_scale_words(millions, "milion", "miliony", "milionów"))
    if thousands:
        parts.append(_scale_words(thousands, "tysiąc", "tysiące", "tysięcy"))
    if rest:
        parts.append(_block_words(rest))
    return " ".join(p for p in parts if p)


def amount_in_words_pln(value: Decimal) -> str:
    """``słownie: tysiąc dwieście trzydzieści cztery złote 56/100``"""
    neg, i, f = _split(value)
    whole = int(i)
    words = integer_to_polish_words(whole)
    unit = _plural_form(whole, "złoty", "złote", "złotych")
    sign = "minus " if neg else ""
    return f"słownie: {sign}{words} {unit} {f}/100"
