"""Fixed buyer identities and address/party construction helpers.

DATA_SPEC.md R0.6: the archive belongs to the client, who is always the
buyer. The generator uses a fixed, small set of buyer identities so the same
buyer recurs across the corpus — a tool that latches onto the recurring
party has picked the wrong one. These are written to
``data/_generator_meta.json`` so acceptance test §10.9 can assert that no
buyer identity ever leaks into ``expected.jsonl`` as a counterparty.
"""

from __future__ import annotations

from .models import Address, Party


def address(
    street: str,
    postal_code: str,
    city: str,
    country_code: str,
    country_line: str | None,
) -> Address:
    return Address(
        street=street,
        postal_code=postal_code,
        city=city,
        country_code=country_code,
        country_line=country_line,
    )


def party(
    name: str,
    tax_id: str | None = None,
    tax_id_rendered: str | None = None,
    addr: Address | None = None,
) -> Party:
    return Party(
        name=name,
        tax_id=tax_id,
        tax_id_rendered=tax_id_rendered if tax_id_rendered is not None else tax_id,
        address=addr,
    )


# Fixed buyer legal entities — the client. Recur across the corpus; never a
# counterparty. NIPs below are fixed, valid-checksum, hand-picked constants
# (not drawn from the seeded RNG) precisely because they must stay constant
# regardless of any change to seller-generation code.
BUYER_MAIN = party(
    name='Spółdzielnia Mieszkaniowa "Zielone Wzgórze" w Krakowie',
    tax_id="6751234567",
    addr=address("ul. Ogrodowa 14", "30-002", "Kraków", "PL", "Polska"),
)

BUYER_SECONDARY = party(
    name='SM "Zielone Wzgórze" — Zarząd Nieruchomości',
    tax_id="6751234567",
    addr=address("ul. Ogrodowa 14", "30-002", "Kraków", "PL", "Polska"),
)

BUYERS: list[Party] = [BUYER_MAIN, BUYER_SECONDARY]


def buyer_identities_meta() -> list[dict]:
    return [
        {"name": b.name, "tax_id": b.tax_id}
        for b in BUYERS
    ]
