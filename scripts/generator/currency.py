"""Currency ground-truth resolution (DATA_SPEC.md §3.2).

The generator uses this to *derive* the expected currency for documents whose
currency is inferred rather than stated, so the precedence rules are encoded
once and exercised consistently across the corpus. This is generator-side
ground truth machinery, not extraction logic — the extractor is expected to
implement (and be scored against) its own version of this table.
"""

from __future__ import annotations

# VAT/registration prefix -> ISO 4217. Explicit table, never a euro-zone
# assumption: EL is Greece, XI is Northern Ireland, and non-euro EU members
# (SE, DK, CZ, HU, RON) must not resolve to EUR.
VAT_PREFIX_TO_CURRENCY: dict[str, str] = {
    "PL": "PLN",
    "DE": "EUR",
    "FR": "EUR",
    "IT": "EUR",
    "ES": "EUR",
    "NL": "EUR",
    "BE": "EUR",
    "AT": "EUR",
    "IE": "EUR",
    "PT": "EUR",
    "FI": "EUR",
    "LU": "EUR",
    "EL": "EUR",  # Greece
    "XI": "GBP",  # Northern Ireland
    "SE": "SEK",
    "DK": "DKK",
    "CZ": "CZK",
    "HU": "HUF",
    "RO": "RON",
    "GB": "GBP",
    "CH": "CHF",
    "NO": "NOK",
}

# Country of registered address -> ISO 4217, used at precedence level 4.
COUNTRY_TO_CURRENCY: dict[str, str] = {
    "PL": "PLN",
    "DE": "EUR",
    "FR": "EUR",
    "IT": "EUR",
    "ES": "EUR",
    "NL": "EUR",
    "BE": "EUR",
    "AT": "EUR",
    "IE": "EUR",
    "PT": "EUR",
    "FI": "EUR",
    "LU": "EUR",
    "GR": "EUR",
    "SE": "SEK",
    "DK": "DKK",
    "CZ": "CZK",
    "HU": "HUF",
    "RO": "RON",
    "GB": "GBP",
    "CH": "CHF",
    "NO": "NOK",
    "US": "USD",
    "CA": "CAD",
}

UNAMBIGUOUS_SYMBOLS = {"zł": "PLN", "€": "EUR", "£": "GBP"}

# Ambiguous symbol + seller country -> currency (precedence level 3).
AMBIGUOUS_SYMBOL_BY_COUNTRY = {
    ("$", "US"): "USD",
    ("$", "CA"): "CAD",
}


def resolve_currency(
    *,
    explicit_code: str | None = None,
    symbol: str | None = None,
    seller_country_code: str | None = None,
    seller_has_address: bool = False,
    seller_vat_prefix: str | None = None,
) -> tuple[str | None, str]:
    """Return ``(currency, source)`` following the strict precedence order.

    ``source`` is one of: explicit, symbol_unambiguous, symbol_ambiguous,
    address, vat_prefix, none — used to tag documents in the manifest.
    """
    if explicit_code:
        return explicit_code, "explicit"

    if symbol and symbol in UNAMBIGUOUS_SYMBOLS:
        return UNAMBIGUOUS_SYMBOLS[symbol], "symbol_unambiguous"

    if symbol and seller_country_code:
        resolved = AMBIGUOUS_SYMBOL_BY_COUNTRY.get((symbol, seller_country_code))
        if resolved:
            return resolved, "symbol_ambiguous"

    if seller_has_address and seller_country_code:
        currency = COUNTRY_TO_CURRENCY.get(seller_country_code)
        if currency:
            return currency, "address"

    if not seller_has_address and seller_vat_prefix:
        currency = VAT_PREFIX_TO_CURRENCY.get(seller_vat_prefix)
        if currency:
            return currency, "vat_prefix"

    return None, "none"
