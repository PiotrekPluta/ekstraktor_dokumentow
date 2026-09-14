"""NIP (Polish tax id) generation, checksum, formatting variants, and decoys.

Independent from anything the extractor will implement (DATA_SPEC.md R0.2):
this module exists purely to produce realistic-looking ground truth and
document text, not to validate untrusted input.
"""

from __future__ import annotations

import random

_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)


def checksum_digit(first_nine: str) -> int:
    total = sum(int(d) * w for d, w in zip(first_nine, _WEIGHTS))
    return total % 11


def is_valid_nip(nip: str) -> bool:
    if len(nip) != 10 or not nip.isdigit():
        return False
    return checksum_digit(nip[:9]) == int(nip[9])


def generate_valid_nip(rng: random.Random) -> str:
    while True:
        first_nine = "".join(str(rng.randint(0, 9)) for _ in range(9))
        if first_nine[0] == "0":
            continue
        check = checksum_digit(first_nine)
        if check == 10:
            continue  # invalid checksum result, redraw
        return first_nine + str(check)


def generate_invalid_nip(rng: random.Random) -> str:
    """A syntactically well-formed NIP whose checksum digit is deliberately wrong."""
    valid = generate_valid_nip(rng)
    wrong_last = (int(valid[9]) + 1) % 10
    return valid[:9] + str(wrong_last)


def generate_regon(rng: random.Random, *, long: bool = False) -> str:
    length = 14 if long else 9
    return "".join(str(rng.randint(0, 9)) for _ in range(length))


def generate_krs(rng: random.Random) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(10))


def generate_iban_pl(rng: random.Random) -> str:
    digits = "".join(str(rng.randint(0, 9)) for _ in range(26))
    return "PL" + digits


def format_iban_spaced(iban: str) -> str:
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


# --- rendering variants for a normalised 10-digit Polish NIP -----------------

def render_plain(nip: str) -> str:
    return nip


def render_dashed(nip: str) -> str:
    # 123-456-78-90
    return f"{nip[0:3]}-{nip[3:6]}-{nip[6:8]}-{nip[8:10]}"


def render_spaced(nip: str) -> str:
    return f"{nip[0:3]} {nip[3:6]} {nip[6:8]} {nip[8:10]}"


def render_pl_prefixed(nip: str) -> str:
    return f"PL{nip}"


def render_pl_prefixed_labeled(nip: str) -> str:
    return f"NIP: PL {nip[0:3]}-{nip[3:6]}-{nip[6:8]}-{nip[8:10]}"


NIP_FORMATTERS = {
    "plain": render_plain,
    "dashed": render_dashed,
    "spaced": render_spaced,
    "pl_prefixed": render_pl_prefixed,
    "pl_prefixed_labeled": render_pl_prefixed_labeled,
}


def render_foreign_vat(country_prefix: str, digits: str) -> str:
    return f"{country_prefix}{digits}"
