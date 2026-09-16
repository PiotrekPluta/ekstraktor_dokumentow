"""Validation and normalisation of a raw LLM extraction (docs/PROJECT_NOTES.md
§8 Stage 6).

`validate_extraction()` is a pure function: parse the model's JSON against
`extractor.schema.ExtractedFields`, normalise each field, and check that
every non-null value actually occurs in the document's own text. It never
calls a model itself — the repair-attempt retry loop that decides whether to
ask the model again, null a field, or quarantine the document belongs to
Stage 7's orchestration loop, which is the only place that already holds an
`LLMClient`. Keeping the model call out of this module is what lets it be
tested with canned JSON strings, no `fake` backend needed, consistent with
every other stage's "tests never call a real model" rule.

Scope boundary: this module validates and normalises whatever the model
returned. It does not re-derive currency from the seller-address/VAT-prefix
precedence chain (docs/DATA_SPEC.md §3.2 levels 3-5) — that needs document
context (seller address, VAT ID) this module doesn't have, and belongs to
prompt/extraction design in Stage 7. What this module does, per
docs/PROJECT_NOTES.md §8 verbatim, is narrower: an ISO 4217 allowlist plus
normalisation of the handful of symbols that are unambiguous everywhere
(``zł``, ``€``, ``£``). A bare ``$`` is deliberately never auto-mapped to
USD: docs/DATA_SPEC.md §3.2 classifies it as an *ambiguous* symbol (used by
USD, CAD, AUD, NZD, HKD, SGD, MXN...) resolved only by seller-country
context this module doesn't have, and the corpus's own ground truth
(``Pump_replacement_estimate.eml``, tagged ``currency-dollar-no-signal-null``
in data/MANIFEST.md) confirms a bare ``$`` with no other signal is expected
to resolve to ``null``, not ``USD``.

Known, stated limitation: the "occurs in source text" check defends against
plain hallucination (a value the model invented from nowhere), but does
*not* by itself distinguish a document's genuine business content from a
value inside an injected fake-JSON-block decoy (docs/DATA_SPEC.md §5) — a
decoy value technically "occurs in the text" too. The structural defense
against that injection class is Stage 7's single-row-scoped parameterised
write (extractor.db's per-document transaction), not this content check;
this check only ever catches values that don't appear anywhere at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import ValidationError

from extractor.candidates import (
    find_amount_candidates,
    find_date_candidates,
    find_nip_candidates,
)
from extractor.normalize import normalize_text
from extractor.schema import ExtractedFields

_NIP_DIGIT_COUNT = 10
_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)

# Symbols that mean exactly one currency everywhere (docs/DATA_SPEC.md
# §3.2 "unambiguous symbol", precedence level 2). `$` is deliberately
# excluded — see the module docstring.
_UNAMBIGUOUS_SYMBOLS = {"zł": "PLN", "€": "EUR", "£": "GBP"}

# Active ISO 4217 alphabetic currency codes. An allowlist, not an exhaustive
# authority on world currencies: its job is to reject a hallucinated code
# (e.g. a model inventing "PLZ"), not to be the canonical registry.
_ISO_4217_CODES = frozenset(
    [
        "AED",
        "AFN",
        "ALL",
        "AMD",
        "ANG",
        "AOA",
        "ARS",
        "AUD",
        "AWG",
        "AZN",
        "BAM",
        "BBD",
        "BDT",
        "BGN",
        "BHD",
        "BIF",
        "BMD",
        "BND",
        "BOB",
        "BRL",
        "BSD",
        "BTN",
        "BWP",
        "BYN",
        "BZD",
        "CAD",
        "CDF",
        "CHF",
        "CLP",
        "CNY",
        "COP",
        "CRC",
        "CUP",
        "CVE",
        "CZK",
        "DJF",
        "DKK",
        "DOP",
        "DZD",
        "EGP",
        "ERN",
        "ETB",
        "EUR",
        "FJD",
        "FKP",
        "GBP",
        "GEL",
        "GHS",
        "GIP",
        "GMD",
        "GNF",
        "GTQ",
        "GYD",
        "HKD",
        "HNL",
        "HTG",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "IQD",
        "IRR",
        "ISK",
        "JMD",
        "JOD",
        "JPY",
        "KES",
        "KGS",
        "KHR",
        "KMF",
        "KPW",
        "KRW",
        "KWD",
        "KYD",
        "KZT",
        "LAK",
        "LBP",
        "LKR",
        "LRD",
        "LSL",
        "LYD",
        "MAD",
        "MDL",
        "MGA",
        "MKD",
        "MMK",
        "MNT",
        "MOP",
        "MRU",
        "MUR",
        "MVR",
        "MWK",
        "MXN",
        "MYR",
        "MZN",
        "NAD",
        "NGN",
        "NIO",
        "NOK",
        "NPR",
        "NZD",
        "OMR",
        "PAB",
        "PEN",
        "PGK",
        "PHP",
        "PKR",
        "PLN",
        "PYG",
        "QAR",
        "RON",
        "RSD",
        "RUB",
        "RWF",
        "SAR",
        "SBD",
        "SCR",
        "SDG",
        "SEK",
        "SGD",
        "SHP",
        "SLE",
        "SOS",
        "SRD",
        "SSP",
        "STN",
        "SYP",
        "SZL",
        "THB",
        "TJS",
        "TMT",
        "TND",
        "TOP",
        "TRY",
        "TTD",
        "TWD",
        "TZS",
        "UAH",
        "UGX",
        "USD",
        "UYU",
        "UZS",
        "VES",
        "VND",
        "VUV",
        "WST",
        "XAF",
        "XCD",
        "XOF",
        "XPF",
        "YER",
        "ZAR",
        "ZMW",
        "ZWL",
    ]
)


class ExtractionUnparseable(Exception):
    """The document as a whole needs a repair attempt: `raw_json` didn't
    parse into `ExtractedFields` at all (bad JSON, wrong types, or a
    `doc_type` outside the closed enum). Distinct from a single field
    failing its occurs-in-source check, which nulls that field only."""


@dataclass(frozen=True)
class ValidationOutcome:
    fields: ExtractedFields | None
    nulled_fields: list[str] = field(default_factory=list)
    needs_repair: bool = False
    quarantine_reason: str | None = None
    # Set only alongside needs_repair=True: human-readable reason the raw
    # response was rejected, for Stage 7's build_repair_prompt() to quote
    # back to the model ("your previous response was invalid: <error>").
    error: str | None = None


def normalize_nip(raw: str) -> str:
    """Digits only. docs/DATA_SPEC.md §3.1: `1234567890`, `123-456-78-90`,
    `123 456 78 90`, `PL1234567890`, `NIP: PL 123-45-67-890` all normalise
    the same way. Does not distinguish a NIP from a same-shaped REGON/KRS/
    invoice-number decoy — that's an extraction concern, not this one's."""
    return re.sub(r"\D", "", raw)


def nip_checksum_valid(nip: str) -> bool:
    """Mod-11 with weights 6,5,7,2,3,4,5,6,7 (docs/PROJECT_NOTES.md §8).

    Informational only: confirmed against the real ground truth
    (`Faktura_FV_2024_09_019.docx`, tagged `nip-checksum-fail` in
    data/MANIFEST.md) that a failed checksum is still emitted as found,
    never nulled or rejected — flagging it is a downstream consumer's
    business, not this function's.
    """
    if len(nip) != _NIP_DIGIT_COUNT or not nip.isdigit():
        return False
    total = sum(weight * int(digit) for weight, digit in zip(_NIP_WEIGHTS, nip))
    return total % 11 == int(nip[-1])


def normalize_amount(raw: str | Decimal) -> Decimal | None:
    """Quantise to 0.01, `ROUND_HALF_UP`. Accepts a negative value as-is
    (correction invoices, docs/DATA_SPEC.md §3.2)."""
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except InvalidOperation:
        return None
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_currency(raw: str) -> str | None:
    """Uppercase ISO 4217 allowlist, plus the closed unambiguous-symbol
    map (`zł`->PLN, `€`->EUR, `£`->GBP). A bare `$`, or anything else that
    isn't already a real ISO code, returns None rather than guessing — see
    the module docstring for why `$` specifically is never auto-mapped."""
    stripped = raw.strip()
    if stripped in _UNAMBIGUOUS_SYMBOLS:
        return _UNAMBIGUOUS_SYMBOLS[stripped]
    upper = stripped.upper()
    if upper in _ISO_4217_CODES:
        return upper
    return None


def occurs_in_source(value: str, source_text: str) -> bool:
    """The anti-hallucination/anti-injection-lite check
    docs/PROJECT_NOTES.md §8 names: does this value actually appear in the
    document's own text? Case/whitespace/diacritics-composition-insensitive
    (reuses `normalize_text`'s NFC+casefold treatment, already proven for
    dedup) so a differently-cased or differently-spaced rendering still
    counts as present. For NIP-shaped values specifically, also matches
    against every NIP-shaped span `find_nip_candidates` finds in the
    source, digit-only, so a differently-formatted-but-same-digits NIP
    (`123-456-78-90` in source vs `1234567890` from the model) still
    counts. See the module docstring for this check's known limitation
    against fake-JSON-block injection.
    """
    normalized_source = normalize_text(source_text)
    normalized_value = normalize_text(value)
    if normalized_value and normalized_value in normalized_source:
        return True

    digits_only = re.sub(r"\D", "", value)
    if len(digits_only) == _NIP_DIGIT_COUNT:
        candidate_digits = {
            re.sub(r"\D", "", m.group()) for m in find_nip_candidates(source_text)
        }
        if digits_only in candidate_digits:
            return True

    return False


def _parse(raw_json: str) -> ExtractedFields:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ExtractionUnparseable(str(exc)) from exc
    try:
        return ExtractedFields.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionUnparseable(str(exc)) from exc


def validate_extraction(raw_json: str, source_text: str) -> ValidationOutcome:
    """Parse `raw_json` against `ExtractedFields`, normalise every field,
    and null out any value that fails its field-appropriate corroboration
    check.

    A document-level failure (`needs_repair=True`, no `fields`) happens
    only when `raw_json` doesn't parse into a valid `ExtractedFields` at
    all — matching `extractor.db`'s own CHECK constraint that `done`
    requires `doc_type` to be set, so a document that can't even produce a
    valid `doc_type` cannot become `done` and must go through Stage 7's
    repair-then-quarantine path instead. A single field failing its check
    only nulls that field; the document still completes normally, matching
    how routinely a field is legitimately `null` in a real document.

    Per-field corroboration is deliberately not the same strict literal
    "occurs in source" test for every field, because a literal substring
    check is only meaningful for values the model is expected to copy
    verbatim:

    - `counterparty_name` / `counterparty_tax_id`: strict `occurs_in_source`
      (case/format-insensitive). These are exactly the two fields
      docs/PROJECT_NOTES.md §8 names as the anti-injection target ("blocks
      both hallucination and an injected 'use this other company's NIP'"),
      and both are normally copied close to verbatim from the source.
    - `issue_date` / `due_date`: NOT literally source-matched. Requirement:
      docs/DATA_SPEC.md §3.3's derived-due-date case ("termin płatności: 14
      dni od daty wystawienia" -> a *computed* date) means the correct
      value legitimately never appears verbatim in the source at all —
      literal matching would null a correct extraction. Instead, the field
      is nulled only if the source contains no date-shaped text whatsoever
      (`find_date_candidates`), a weak but cheap defence against a wholesale
      fabrication on a document with no dates in it.
    - `gross_amount`: same reasoning — a normalised `Decimal` like
      `1234.56` will not literally match a source rendering like
      `1 234,56 zł` (different separators, currency suffix). Nulled only if
      the source has no amount-shaped text at all (`find_amount_candidates`).
    - `currency`: validated by the ISO 4217 allowlist / symbol map instead
      of source occurrence — see `normalize_currency` and the module
      docstring for why a bare `$` is never auto-resolved.
    """
    try:
        parsed = _parse(raw_json)
    except ExtractionUnparseable as exc:
        return ValidationOutcome(fields=None, needs_repair=True, error=str(exc))

    nulled: list[str] = []
    data = parsed.model_dump()

    if data["counterparty_name"] is not None and not occurs_in_source(
        data["counterparty_name"], source_text
    ):
        data["counterparty_name"] = None
        nulled.append("counterparty_name")

    if data["counterparty_tax_id"] is not None:
        normalized_nip = normalize_nip(data["counterparty_tax_id"])
        if normalized_nip and occurs_in_source(normalized_nip, source_text):
            data["counterparty_tax_id"] = normalized_nip
        else:
            data["counterparty_tax_id"] = None
            nulled.append("counterparty_tax_id")

    if data["issue_date"] is not None and not find_date_candidates(source_text):
        data["issue_date"] = None
        nulled.append("issue_date")

    if data["due_date"] is not None and not find_date_candidates(source_text):
        data["due_date"] = None
        nulled.append("due_date")

    if data["gross_amount"] is not None:
        if find_amount_candidates(source_text):
            data["gross_amount"] = normalize_amount(data["gross_amount"])
        else:
            data["gross_amount"] = None
            nulled.append("gross_amount")

    if data["currency"] is not None:
        normalized_currency = normalize_currency(str(data["currency"]))
        data["currency"] = normalized_currency
        if normalized_currency is None:
            nulled.append("currency")

    if data["currency"] is None and data["gross_amount"] is not None:
        # Confirmed ground truth (Pump_replacement_estimate.eml): an amount
        # without a resolvable currency is not a valid extraction on its
        # own — null the amount too, not just the currency.
        data["gross_amount"] = None
        nulled.append("gross_amount")

    if not data["summary"].strip():
        return ValidationOutcome(
            fields=None, needs_repair=True, error="summary is empty"
        )

    fields = ExtractedFields.model_validate(data)
    return ValidationOutcome(fields=fields, nulled_fields=nulled)
