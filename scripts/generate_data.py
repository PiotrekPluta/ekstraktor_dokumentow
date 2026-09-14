#!/usr/bin/env python3
"""Generate the synthetic corpus described in docs/DATA_SPEC.md.

Usage: uv run python scripts/generate_data.py [--seed N] [--out data]
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from generator import amounts, content, corrupt, duplicates, identities, manifest, nip
from generator import dates as gdates
from generator.models import DocType, Document
from generator.render_docx import render_docx
from generator.render_eml import render_eml
from generator.render_html import render_html
from generator.render_pdf import render_pdf
from generator.render_txt import render_txt, render_txt_string

SEED_DEFAULT = 20260101
FIXED_DATETIME = datetime(2026, 1, 1, tzinfo=UTC)

DECISIONS = [
    "Failed mod-11 NIP checksum is emitted **as found**, not nulled — flagging "
    "it is a downstream validator's job, not the generator's to hide (see inv07).",
    "A bare `$` on an English document with no address/VAT signal resolves to "
    "`null`, per precedence level 6 (see corr03).",
    "`gross_amount` for contracts/offers is the single total figure the "
    "document itself presents as the bottom line (contract fee / quoted price).",
    "Contract `due_date` is the end of the contract term, not a payment date.",
    "Two byte-identical corrupt files would count as one `expected.jsonl` "
    "line (dedup runs before parsing) — not exercised in this sample corpus.",
    "3 sellers omit their postal address entirely (inv04, offer03, offer04), "
    "not 2 as DATA_SPEC.md §3.4 suggests: §3.2's required cases need three "
    "distinct no-address currency scenarios (DE-VAT→EUR, SE-VAT→SEK, "
    "no-VAT-no-address→null) that cannot be collapsed into two documents "
    "without contradicting each other.",
]


def _party(name, tax_id=None, tax_id_rendered=None, addr=None):
    return identities.party(name, tax_id=tax_id, tax_id_rendered=tax_id_rendered, addr=addr)


def _addr(street, postal, city, country_code, country_line):
    return identities.address(street, postal, city, country_code, country_line)


class MonthCycle:
    """Cycles issue dates through all 12 Polish genitive month names across
    the corpus (DATA_SPEC.md §3.3)."""

    def __init__(self):
        self.i = 0

    def next_date(self, year: int = 2024, day: int = 12) -> date:
        month = (self.i % 12) + 1
        self.i += 1
        return date(year, month, day)


MONTHS = MonthCycle()

BUYER = identities.BUYER_MAIN


# ============================================================ INVOICES (10) =

def build_invoices(rng: random.Random) -> list[Document]:
    docs = []

    # --- inv01 [pdf] — net+gross labeled, zł symbol, quotes in name --------
    s1 = _party(
        'Zakład Usługowy "Merkury" Sp. z o.o.',
        tax_id=nip.generate_valid_nip(rng),
        addr=_addr("ul. Przemysłowa 8", "31-587", "Kraków", "PL", "Polska"),
    )
    issue1 = date(2024, 3, 12)
    items1 = [("Przegląd techniczny windy", 1, Decimal("1200.00")), ("Dojazd serwisowy", 2, Decimal("150.00"))]
    tbl1, net1, vat1, gross1 = content.invoice_items_table(items1, Decimal("0.23"))
    blocks1 = [
        content.heading("Faktura VAT nr FV/2024/03/018"),
        *content.party_lines(s1, "Sprzedawca:", tax_id_display=nip.render_plain(s1.tax_id)),
        content.spacer(),
        *content.party_lines(BUYER, "Nabywca:"),
        content.spacer(),
        content.paragraph(f"Data wystawienia: {gdates.dotted(issue1)}"),
        content.paragraph(f"Data sprzedaży: {gdates.dotted(issue1)}"),
        content.paragraph("Termin płatności: 14.04.2024"),
        content.spacer(),
        tbl1,
        content.paragraph(f"Do zapłaty: {amounts.format_pl_space_zl(gross1)}", bold=True),
    ]
    docs.append(Document(
        doc_id="inv01", doc_type=DocType.INVOICE, render_format="pdf", language="pl",
        counterparty=s1, issue_date=issue1, due_date=date(2024, 4, 14),
        gross_amount=gross1, currency="PLN",
        summary="Faktura za przegląd techniczny windy wystawiona przez Zakład Usługowy \"Merkury\".",
        blocks=blocks1,
        tags=["amount-labeled-net-gross", "currency-symbol-unambiguous-zl", "nip-format-plain",
              "name-with-quotes", "legal-form-sp-zoo", "date-format-dotted"],
    ))

    # --- inv02 [docx] — correction invoice, negative gross, iso date -------
    s2 = _party(
        "Bud-Serwis S.A.",
        tax_id=nip.generate_valid_nip(rng),
        addr=_addr("ul. Kolejowa 3", "00-950", "Warszawa", "PL", "Polska"),
    )
    issue2 = date(2024, 5, 20)
    sale2 = date(2024, 5, 18)
    gross2 = Decimal("-450.00")
    blocks2 = [
        content.heading("Faktura korygująca nr FK/2024/05/003 do FV/2024/03/018"),
        *content.party_lines(s2, "Sprzedawca:", tax_id_display=nip.render_dashed(s2.tax_id)),
        content.spacer(),
        *content.party_lines(BUYER, "Nabywca:"),
        content.spacer(),
        content.paragraph(f"Data wystawienia: {gdates.iso(issue2)}"),
        content.sale_date_line(gdates.iso(sale2)),
        content.print_date_footer(gdates.iso(issue2)),
        content.spacer(),
        content.paragraph("Powód korekty: zwrot części materiałów."),
        content.paragraph(f"Kwota korekty (brutto): {amounts.format_pl_dot_pln(gross2)}", bold=True),
    ]
    docs.append(Document(
        doc_id="inv02", doc_type=DocType.INVOICE, render_format="docx", language="pl",
        counterparty=s2, issue_date=issue2, due_date=None,
        gross_amount=gross2, currency="PLN",
        summary="Faktura korygująca Bud-Serwis S.A. zmniejszająca kwotę o zwrócone materiały.",
        blocks=blocks2,
        tags=["correction-invoice", "negative-amount", "currency-only-PLN-code",
              "nip-format-dashed", "legal-form-sa", "date-format-iso",
              "decoy-print-date-footer", "decoy-sale-date"],
    ))

    # --- inv03 [pdf] — English, Toronto seller, ambiguous $ -> CAD ---------
    s3 = _party(
        "Northern Supplies Ltd",
        addr=_addr("123 King St W", "M5H 1A1", "Toronto, ON", "CA", "Canada"),
    )
    issue3 = date(2024, 6, 4)
    items3 = [("Annual maintenance service", 1, Decimal("980.00"))]
    tbl3, net3, vat3, gross3 = content.invoice_items_table(
        items3, Decimal("0.0"), header=("No.", "Description", "Qty", "Unit price", "Amount")
    )
    blocks3 = [
        content.heading("Invoice INV-2024-0044"),
        *content.party_lines(s3, "Vendor:"),
        content.spacer(),
        *content.party_lines(BUYER, "Bill to:"),
        content.spacer(),
        content.paragraph(f"Issue date: {gdates.english_long(issue3)}"),
        content.paragraph(f"Due date: {gdates.us_slash_ambiguous(date(2024, 6, 18))}"),
        content.spacer(),
        tbl3,
        content.paragraph(f"Total due: {amounts.format_dollar(net3)}", bold=True),
    ]
    docs.append(Document(
        doc_id="inv03", doc_type=DocType.INVOICE, render_format="pdf", language="en",
        counterparty=s3, issue_date=issue3, due_date=date(2024, 6, 18),
        gross_amount=net3, currency="CAD",
        summary="Invoice from Northern Supplies Ltd for annual maintenance service.",
        blocks=blocks3,
        tags=["currency-symbol-ambiguous", "english", "invoice-english-1",
              "legal-form-ltd", "date-format-english-long", "date-format-us-slash-ambiguous"],
    ))

    # --- inv04 [eml] — DE VAT id, no address, English, currency via prefix -
    s4 = _party(
        "Bergmann Fensterbau GmbH",
        tax_id="DE123456789",
        tax_id_rendered=nip.render_foreign_vat("DE", "123456789"),
    )
    issue4 = date(2024, 7, 9)
    body4 = (
        "Dear Sir or Madam,\n\n"
        "Please find below the details of our invoice for the window frame "
        "repair works completed on site.\n\n"
        "Invoice no.: RE-2024-0091\n"
        f"Issue date: {gdates.iso(issue4)}\n"
        "Amount due: 1850.00\n"
        f"VAT ID: {s4.tax_id_rendered}\n\n"
        "Kind regards,\nBergmann Fensterbau GmbH"
    )
    docs.append(Document(
        doc_id="inv04", doc_type=DocType.INVOICE, render_format="eml", language="en",
        counterparty=s4, issue_date=issue4, due_date=None,
        gross_amount=Decimal("1850.00"), currency="EUR",
        summary="Invoice email from Bergmann Fensterbau GmbH for window frame repairs.",
        blocks=[],
        extra={"eml": {
            "subject": "Invoice RE-2024-0091",
            "from_addr": "billing@bergmann-fensterbau.example",
            "to_addr": "administracja@smzielonewzgorze.example",
            "from_display": "Bergmann Fensterbau GmbH",
            "body_text": body4,
        }},
        tags=["currency-from-vat-prefix", "foreign-vat-id", "english", "invoice-english-2",
              "legal-form-gmbh", "no-address-1-of-3"],
    ))

    # --- inv05 [txt, cp1250] — buyer NIP first, decoys, derived due date ---
    s5_nip = nip.generate_valid_nip(rng)
    s5 = _party(
        "P.P.H.U. Kowalski Marek",
        tax_id=s5_nip,
        addr=_addr("ul. Długa 12", "61-003", "Poznań", "PL", "Polska"),
    )
    issue5 = MONTHS.next_date()
    due5 = gdates.derive_due_date(issue5, 14)
    items5 = [("Naprawa instalacji hydraulicznej", 1, Decimal("560.00"))]
    tbl5, net5, vat5, gross5 = content.invoice_items_table(items5, Decimal("0.23"))
    regon5 = nip.generate_regon(rng)
    krs5 = nip.generate_krs(rng)
    iban5 = nip.generate_iban_pl(rng)
    blocks5 = [
        content.heading("Faktura VAT nr FV/2024/06/077"),
        content.paragraph(f"NIP nabywcy: {nip.render_plain(BUYER.tax_id)}"),
        *content.party_lines(BUYER, "Nabywca:", tax_id_display=""),
        content.spacer(),
        *content.party_lines(s5, "Sprzedawca:", tax_id_display=nip.render_spaced(s5.tax_id)),
        *content.decoy_ids_block(rng, regon=regon5, krs=krs5, iban=nip.format_iban_spaced(iban5)),
        content.small("Nr dokumentu wewnętrznego: 5502190034"),
        content.spacer(),
        content.paragraph(f"Data wystawienia: {gdates.polish_long(issue5)}"),
        content.paragraph("Termin płatności: 14 dni od daty wystawienia"),
        content.spacer(),
        tbl5,
        content.paragraph(f"Do zapłaty: {amounts.format_pl_space_zl(gross5)}", bold=True),
    ]
    docs.append(Document(
        doc_id="inv05", doc_type=DocType.INVOICE, render_format="txt", language="pl",
        counterparty=s5, issue_date=issue5, due_date=due5,
        gross_amount=gross5, currency="PLN",
        summary="Faktura P.P.H.U. Kowalski Marek za naprawę instalacji hydraulicznej.",
        blocks=blocks5,
        extra={"encoding": "cp1250"},
        tags=["buyer-nip-first", "decoy-regon", "decoy-krs", "decoy-iban",
              "decoy-invoice-number-tendigit", "due-date-derived",
              "currency-from-seller-country", "nip-format-spaced",
              "legal-form-pphu", "date-format-polish-long", "encoding-cp1250"],
    ))

    # --- inv06 [pdf] — buyer NIP more prominent, buyer precedes seller -----
    s6 = _party(
        "Elektro-Mont sp. z o.o. sp.k.",
        tax_id=nip.generate_valid_nip(rng),
        addr=_addr("ul. Wesoła 5", "50-001", "Wrocław", "PL", "Polska"),
    )
    decoy_buyer6 = _party(
        "MB Immobilien Verwaltung GmbH",
        tax_id="DE998877665",
        addr=_addr("Alexanderplatz 3", "10178", "Berlin", "DE", "Niemcy"),
    )
    issue6 = MONTHS.next_date()
    items6 = [("Przegląd instalacji elektrycznej budynku", 1, Decimal("3400.00"))]
    tbl6, net6, vat6, gross6 = content.invoice_items_table(items6, Decimal("0.23"))
    blocks6 = [
        content.heading("Faktura VAT nr FV/2024/08/052"),
        content.paragraph("Nabywca:", bold=True),
        content.paragraph(decoy_buyer6.name, bold=True, size="large"),
        content.paragraph(f"NIP: {decoy_buyer6.tax_id_rendered}", bold=True),
        content.spacer(),
        *content.party_lines(s6, "Sprzedawca:", tax_id_display=nip.render_plain(s6.tax_id)),
        content.spacer(),
        content.paragraph(f"Data wystawienia: {gdates.polish_long(issue6)}"),
        content.spacer(),
        tbl6,
        content.paragraph(f"Do zapłaty: {amounts.format_nbsp(gross6, 'zł')}", bold=True),
        content.paragraph(amounts.amount_in_words_pln(gross6)),
    ]
    docs.append(Document(
        doc_id="inv06", doc_type=DocType.INVOICE, render_format="pdf", language="pl",
        counterparty=s6, issue_date=issue6, due_date=None,
        gross_amount=gross6, currency="PLN",
        summary="Faktura Elektro-Mont za przegląd instalacji elektrycznej budynku.",
        blocks=blocks6,
        tags=["buyer-nip-prominent", "buyer-decoy-prominent", "buyer-precedes-seller",
              "amount-nbsp-separator", "amount-in-words", "currency-buyer-address-ignored",
              "legal-form-spzoospk", "date-format-polish-long"],
    ))

    # --- inv07 [docx] — NIP fails mod-11 checksum ---------------------------
    s7 = _party(
        "Instalatorstwo Nowak",
        tax_id=nip.generate_invalid_nip(rng),
        addr=_addr("ul. Rzemieślnicza 9", "40-004", "Katowice", "PL", "Polska"),
    )
    issue7 = MONTHS.next_date()
    items7 = [("Usługa instalatorska", 1, Decimal("890.00"))]
    tbl7, net7, vat7, gross7 = content.invoice_items_table(items7, Decimal("0.23"))
    blocks7 = [
        content.heading("Faktura VAT nr FV/2024/09/019"),
        *content.party_lines(s7, "Sprzedawca:", tax_id_display=nip.render_dashed(s7.tax_id)),
        content.spacer(),
        *content.party_lines(BUYER, "Nabywca:"),
        content.spacer(),
        content.paragraph(f"Data wystawienia: {gdates.polish_long(issue7)}"),
        content.spacer(),
        tbl7,
        content.paragraph(f"Do zapłaty: {amounts.format_pl_space_zl(gross7)}", bold=True),
    ]
    docs.append(Document(
        doc_id="inv07", doc_type=DocType.INVOICE, render_format="docx", language="pl",
        counterparty=s7, issue_date=issue7, due_date=None,
        gross_amount=gross7, currency="PLN",
        summary="Faktura Instalatorstwo Nowak za usługę instalatorską.",
        blocks=blocks7,
        tags=["nip-checksum-fail", "date-format-polish-long"],
    ))

    # --- inv08 [pdf] — multi-page, per-page subtotals, final page total ----
    s8 = _party(
        "TransLogist Sp. z o.o.",
        tax_id=nip.generate_valid_nip(rng),
        addr=_addr("ul. Portowa 22", "80-560", "Gdańsk", "PL", "Polska"),
    )
    issue8 = MONTHS.next_date()
    page_items = [
        [("Transport kontenera - trasa A", 1, Decimal("2100.00")), ("Rozładunek", 1, Decimal("300.00"))],
        [("Transport kontenera - trasa B", 1, Decimal("1950.00")), ("Magazynowanie 3 dni", 3, Decimal("80.00"))],
    ]
    blocks8 = [
        content.heading("Faktura VAT nr FV/2024/10/205"),
        *content.party_lines(s8, "Sprzedawca:", tax_id_display=nip.render_plain(s8.tax_id)),
        content.spacer(),
        *content.party_lines(BUYER, "Nabywca:"),
        content.spacer(),
        content.paragraph(f"Data wystawienia: {gdates.polish_long(issue8)}"),
        content.page_count_decoy(3),
        content.spacer(),
    ]
    running_net = Decimal("0.00")
    for i, items in enumerate(page_items, start=1):
        tbl, net_p, vat_p, gross_p = content.invoice_items_table(items, Decimal("0.23"))
        running_net += net_p
        blocks8.append(content.paragraph(f"Strona {i}", bold=True))
        blocks8.append(tbl)
        blocks8.append(content.paragraph(f"Suma częściowa netto (strona {i}): {net_p:.2f}"))
        blocks8.append(content.page_break())
    vat_total = amounts.quantize(running_net * Decimal("0.23"))
    gross_total8 = amounts.quantize(running_net + vat_total)
    blocks8 += [
        content.paragraph("Podsumowanie całości zamówienia", bold=True),
        content.table([
            ["Netto razem", f"{running_net:.2f}"],
            ["VAT 23%", f"{vat_total:.2f}"],
            ["Brutto razem", f"{gross_total8:.2f}"],
        ]),
        content.paragraph(f"Do zapłaty: {amounts.format_narrow_nbsp(gross_total8, 'PLN')}", bold=True),
    ]
    docs.append(Document(
        doc_id="inv08", doc_type=DocType.INVOICE, render_format="pdf", language="pl",
        counterparty=s8, issue_date=issue8, due_date=None,
        gross_amount=gross_total8, currency="PLN",
        summary="Wielostronicowa faktura TransLogist za usługi transportowe.",
        blocks=blocks8,
        tags=["amount-multipage-subtotal", "amount-narrow-nbsp", "decoy-page-count",
              "date-format-polish-long"],
    ))

    # --- inv09/inv10 [html] — near-duplicate template pair (§4 case 9) -----
    s9 = _party(
        "Serwis-Tech Marcin Wiśniewski",
        tax_id=nip.generate_valid_nip(rng),
        addr=_addr("ul. Kwiatowa 4", "20-001", "Lublin", "PL", "Polska"),
    )
    issue9 = MONTHS.next_date()

    def near_dup_invoice_blocks(invoice_no: str, unit_price: Decimal):
        items = [("Konserwacja domofonu", 1, unit_price)]
        tbl, net_, vat_, gr = content.invoice_items_table(items, Decimal("0.23"))
        return [
            content.heading(f"Faktura VAT nr {invoice_no}"),
            *content.party_lines(s9, "Sprzedawca:", tax_id_display=nip.render_pl_prefixed(s9.tax_id)),
            content.spacer(),
            *content.party_lines(BUYER, "Nabywca:"),
            content.spacer(),
            content.paragraph(f"Data wystawienia: {gdates.iso(issue9)}"),
            content.spacer(),
            tbl,
            content.paragraph(f"Do zapłaty: {amounts.format_pl_dot_pln(gr)}", bold=True),
        ], gr

    blocks9, gross9 = near_dup_invoice_blocks("FV/2024/07/301", Decimal("300.00"))
    docs.append(Document(
        doc_id="inv09", doc_type=DocType.INVOICE, render_format="html", language="pl",
        counterparty=s9, issue_date=issue9, due_date=None,
        gross_amount=gross9, currency="PLN",
        summary="Faktura Serwis-Tech za konserwację domofonu (FV/2024/07/301).",
        blocks=blocks9,
        tags=["near-dup-different-doc", "currency-only-PLN-code", "nip-format-pl-prefixed"],
    ))

    blocks10, gross10 = near_dup_invoice_blocks("FV/2024/07/302", Decimal("314.00"))
    docs.append(Document(
        doc_id="inv10", doc_type=DocType.INVOICE, render_format="html", language="pl",
        counterparty=s9, issue_date=issue9, due_date=None,
        gross_amount=gross10, currency="PLN",
        summary="Faktura Serwis-Tech za konserwację domofonu (FV/2024/07/302).",
        blocks=blocks10,
        tags=["near-dup-different-doc", "currency-only-PLN-code"],
    ))

    return docs


# =========================================================== CONTRACTS (6) =

def build_contracts(rng: random.Random) -> list[Document]:
    docs = []

    # --- contract01 [pdf] — long document, NIP on page ~280, total on final
    c1 = _party(
        "BudMax Serwis Sp. z o.o.",
        tax_id=nip.generate_valid_nip(rng),
        addr=_addr("ul. Przemysłowa 44", "93-231", "Łódź", "PL", "Polska"),
    )
    issue_c1 = MONTHS.next_date()
    term_end_c1 = date(2026, 12, 31)
    contract_value_c1 = Decimal("184000.00")
    blocks_c1 = [
        content.heading("Umowa o świadczenie usług konserwacyjnych nr U/2024/11/002"),
        content.paragraph(f"Zawarta dnia {gdates.polish_roman(issue_c1)} pomiędzy stronami wymienionymi w załączniku nr 1."),
        content.spacer(),
        *content.numbered_clauses([
            "Przedmiotem umowy jest świadczenie usług konserwacji technicznej budynku "
            "przy ul. Ogrodowej 14 w Krakowie przez cały okres obowiązywania umowy.",
            "Wykonawca zobowiązuje się do wykonywania przeglądów okresowych zgodnie "
            "z harmonogramem stanowiącym załącznik nr 2 do niniejszej umowy.",
            "The Contractor shall maintain professional liability insurance "
            "throughout the term of this agreement, in an amount not lower than "
            "PLN 500,000, and shall provide proof thereof upon request.",
            f"Umowa zostaje zawarta na czas określony do dnia {gdates.polish_long(term_end_c1)}.",
        ]),
        content.page_break(),
    ]
    for i in range(278):
        blocks_c1.append(content.filler_paragraph(rng, i))
        blocks_c1.append(content.page_break())
    blocks_c1 += [
        content.paragraph("Załącznik nr 1 — dane stron umowy", bold=True),
        content.paragraph(f"Wykonawca: {c1.name}"),
        content.paragraph(f"{c1.address.street}, {c1.address.postal_code} {c1.address.city}"),
        content.paragraph(f"NIP: {nip.render_plain(c1.tax_id)}"),
        content.page_break(),
    ]
    for i in range(278, 283):
        blocks_c1.append(content.filler_paragraph(rng, i))
        blocks_c1.append(content.page_break())
    blocks_c1 += [
        content.paragraph("Załącznik nr 3 — wynagrodzenie", bold=True),
        content.paragraph(
            f"Łączne wynagrodzenie Wykonawcy z tytułu realizacji niniejszej umowy "
            f"wynosi {amounts.format_pl_space_zl(contract_value_c1)} za cały okres obowiązywania."
        ),
        *content.signature_block(c1, BUYER),
    ]
    docs.append(Document(
        doc_id="contract01", doc_type=DocType.CONTRACT, render_format="pdf", language="pl",
        counterparty=c1, issue_date=issue_c1, due_date=term_end_c1,
        gross_amount=contract_value_c1, currency="PLN",
        summary="Wieloletnia umowa konserwacyjna z BudMax Serwis na budynek przy ul. Ogrodowej 14.",
        blocks=blocks_c1,
        tags=["long-document", "nip-page-280", "amount-final-page", "quotes-english-clause",
              "contract-due-date-term-end", "legal-form-sp-zoo", "date-format-roman"],
    ))

    # --- contract02 [pdf] — Kraków address + DE VAT prefix: address wins ---
    c2 = _party(
        "Zielona Instalacja Sp. z o.o.",
        tax_id="DE123456789",
        tax_id_rendered=nip.render_foreign_vat("DE", "123456789"),
        addr=_addr("ul. Wawelska 10", "31-052", "Kraków", "PL", "Polska"),
    )
    issue_c2 = MONTHS.next_date()
    term_end_c2 = date(2025, 6, 30)
    value_c2 = Decimal("42000.00")
    blocks_c2 = [
        content.heading("Umowa serwisowa nr U/2024/12/017"),
        *content.party_lines(c2, "Wykonawca:", tax_id_display=c2.tax_id_rendered),
        content.spacer(),
        *content.party_lines(BUYER, "Zamawiający:"),
        content.spacer(),
        *content.numbered_clauses([
            f"Umowa zawarta dnia {gdates.polish_long(issue_c2)} na czas określony "
            f"do dnia {gdates.polish_long(term_end_c2)}.",
            "Wykonawca świadczy usługi instalacyjne zgodnie z zakresem opisanym w załączniku.",
            f"Łączne wynagrodzenie Wykonawcy wynosi {value_c2:.2f} za cały okres obowiązywania umowy.",
        ]),
        *content.signature_block(c2, BUYER),
    ]
    docs.append(Document(
        doc_id="contract02", doc_type=DocType.CONTRACT, render_format="pdf", language="pl",
        counterparty=c2, issue_date=issue_c2, due_date=term_end_c2,
        gross_amount=value_c2, currency="PLN",
        summary="Umowa serwisowa z Zielona Instalacja na instalacje wewnętrzne budynku.",
        blocks=blocks_c2,
        tags=["currency-address-beats-prefix", "contract-due-date-term-end",
              "date-format-polish-long"],
    ))

    # --- contract03 [docx] — English, DE address (level4), name only in ----
    # --- signature block, not in the opening recital ------------------------
    c3 = _party(
        "Berliner Fenster & Fassaden GmbH",
        addr=_addr("Musterstraße 12", "10115", "Berlin", "DE", "Germany"),
    )
    issue_c3 = date(2024, 9, 1)
    term_end_c3 = date(2025, 8, 31)
    value_c3 = Decimal("27500.00")
    blocks_c3 = [
        content.heading("Service Agreement No. SA-2024-014"),
        content.paragraph(
            f"This agreement is made on {gdates.english_long(issue_c3)} between the "
            "Client and the Contractor named in the signature block below."
        ),
        content.spacer(),
        *content.numbered_clauses([
            "The Contractor shall supply and install façade window units as "
            "described in Annex A.",
            f"This agreement is concluded for a fixed term ending on "
            f"{gdates.english_long(term_end_c3)}.",
            f"Total remuneration for the Contractor under this agreement is "
            f"{amounts.format_symbol_prefix_eur(value_c3)}.",
        ]),
        *content.signature_block(c3, BUYER, seller_role="Contractor", buyer_role="Client"),
    ]
    docs.append(Document(
        doc_id="contract03", doc_type=DocType.CONTRACT, render_format="docx", language="en",
        counterparty=c3, issue_date=issue_c3, due_date=term_end_c3,
        gross_amount=value_c3, currency="EUR",
        summary="Service agreement with Berliner Fenster & Fassaden GmbH for façade window installation.",
        blocks=blocks_c3,
        tags=["currency-from-seller-country", "contract-english", "legal-form-gmbh",
              "name-in-signature-only", "amount-symbol-prefix-eur",
              "contract-due-date-term-end", "date-format-english-long"],
    ))

    # --- contract04 [html] — no country line, ambiguous city, no VAT -> null
    c4 = _party(
        "Żółtowski i Wspólnicy Sp. z o.o.",
        addr=_addr("ul. Graniczna 2", "43-400", "Cieszyn", "", None),
    )
    issue_c4 = MONTHS.next_date()
    term_end_c4 = date(2025, 3, 31)
    value_c4 = Decimal("15750.00")
    blocks_c4 = [
        content.paragraph(BUYER.name, bold=True, size="large", align="center"),
        content.paragraph(f"{BUYER.address.street}, {BUYER.address.postal_code} {BUYER.address.city}", align="center"),
        content.heading("Umowa o roboty remontowe nr U/2024/09/031"),
        content.spacer(),
        content.small(c4.name),
        content.small(f"{c4.address.street}, {c4.address.postal_code} {c4.address.city}"),
        content.spacer(),
        *content.numbered_clauses([
            f"Umowa zawarta dnia {gdates.polish_long(issue_c4)} na czas określony "
            f"do dnia {gdates.polish_long(term_end_c4)}.",
            "Wykonawca przeprowadzi remont klatki schodowej zgodnie z kosztorysem.",
            f"Wynagrodzenie ryczałtowe wynosi {value_c4:.2f} za całość prac.",
        ]),
        *content.signature_block(c4, BUYER),
    ]
    docs.append(Document(
        doc_id="contract04", doc_type=DocType.CONTRACT, render_format="html", language="pl",
        counterparty=c4, issue_date=issue_c4, due_date=term_end_c4,
        gross_amount=value_c4, currency=None,
        summary="Umowa remontowa z Żółtowski i Wspólnicy na remont klatki schodowej.",
        blocks=blocks_c4,
        tags=["currency-unresolvable", "address-no-country-line", "name-with-diacritics",
              "buyer-decoy-prominent", "buyer-precedes-seller", "contract-due-date-term-end",
              "date-format-polish-long"],
    ))

    # --- contract05/06 [docx] — near-duplicate pair, one clause differs ----
    c5 = _party(
        "Konserwacje Dźwigowe LiftPro Sp. z o.o.",
        tax_id=nip.generate_valid_nip(rng),
        addr=_addr("ul. Dźwigowa 2", "15-001", "Białystok", "PL", "Polska"),
    )
    issue_c5 = MONTHS.next_date()
    term_end_c5 = date(2025, 12, 31)
    value_c5 = Decimal("36000.00")

    def liftpro_blocks(penalty_amount_text: str):
        return [
            content.heading("Umowa konserwacji dźwigów nr U/2024/04/009"),
            *content.party_lines(c5, "Wykonawca:", tax_id_display=nip.render_plain(c5.tax_id)),
            content.spacer(),
            *content.party_lines(BUYER, "Zamawiający:"),
            content.spacer(),
            *content.numbered_clauses([
                f"Umowa zawarta dnia {gdates.polish_long(issue_c5)} na czas określony "
                f"do dnia {gdates.polish_long(term_end_c5)}.",
                "Wykonawca zobowiązuje się do konserwacji dźwigów osobowych zgodnie "
                "z obowiązującymi normami technicznymi.",
                f"Łączne wynagrodzenie Wykonawcy wynosi {value_c5:.2f} za cały okres obowiązywania umowy.",
            ]),
            content.penalty_clause(penalty_amount_text),
            *content.signature_block(c5, BUYER),
        ]

    docs.append(Document(
        doc_id="contract05", doc_type=DocType.CONTRACT, render_format="docx", language="pl",
        counterparty=c5, issue_date=issue_c5, due_date=term_end_c5,
        gross_amount=value_c5, currency="PLN",
        summary="Umowa konserwacji dźwigów z LiftPro (wariant z karą umowną 5000 zł).",
        blocks=liftpro_blocks("5 000,00 zł"),
        tags=["near-dup-different-doc", "decoy-penalty-clause-amount",
              "contract-due-date-term-end", "date-format-polish-long"],
    ))
    docs.append(Document(
        doc_id="contract06", doc_type=DocType.CONTRACT, render_format="docx", language="pl",
        counterparty=c5, issue_date=issue_c5, due_date=term_end_c5,
        gross_amount=value_c5, currency="PLN",
        summary="Umowa konserwacji dźwigów z LiftPro (wariant z karą umowną 7000 zł).",
        blocks=liftpro_blocks("7 000,00 zł"),
        tags=["near-dup-different-doc", "decoy-penalty-clause-amount"],
    ))

    return docs


# ================================================================ OTHER (2) =

def build_other(rng: random.Random) -> list[Document]:
    docs = []

    # --- other01 [pdf] — purely internal memo, no external counterparty ----
    issue1 = date(2024, 6, 1)
    blocks1 = [
        content.heading("Notatka służbowa nr NS/2024/06/002"),
        content.paragraph(f"Data: {gdates.polish_long(issue1)}"),
        content.paragraph("Od: Kierownik ds. technicznych"),
        content.paragraph("Do: Zarząd Spółdzielni"),
        content.spacer(),
        content.paragraph(
            "Informuję, że w bieżącym miesiącu przeprowadzono wewnętrzny przegląd "
            "stanu technicznego klatek schodowych w zasobach spółdzielni. Wyniki "
            "przeglądu nie wskazują na konieczność podjęcia pilnych działań "
            "naprawczych."
        ),
        content.paragraph("Notatkę przekazuję do wiadomości i archiwizacji."),
    ]
    docs.append(Document(
        doc_id="other01", doc_type=DocType.OTHER, render_format="pdf", language="pl",
        counterparty=None, issue_date=issue1, due_date=None,
        gross_amount=None, currency=None,
        summary="Wewnętrzna notatka służbowa z przeglądu stanu technicznego klatek schodowych.",
        blocks=blocks1,
        tags=["doc-type-other-internal-null-counterparty"],
    ))

    # --- other02 [txt] — external vendor technical spec sheet --------------
    vendor = _party("Klimatyka Przemysłowa Sp. z o.o.")
    blocks2 = [
        content.heading("Karta techniczna — centrala wentylacyjna KP-2200"),
        content.paragraph(f"Producent: {vendor.name}"),
        content.spacer(),
        content.table([
            ["Wydajność nominalna", "2200 m3/h"],
            ["Zasilanie", "230V / 50Hz"],
            ["Poziom hałasu", "42 dB(A)"],
            ["Klasa filtracji", "ePM1 55%"],
        ]),
        content.paragraph(
            "Urządzenie przeznaczone do montażu w pomieszczeniach technicznych "
            "budynków wielorodzinnych. Szczegółowe warunki gwarancji określa "
            "odrębna karta gwarancyjna dołączana do urządzenia."
        ),
    ]
    docs.append(Document(
        doc_id="other02", doc_type=DocType.OTHER, render_format="txt", language="pl",
        counterparty=vendor, issue_date=None, due_date=None,
        gross_amount=None, currency=None,
        summary="Karta techniczna centrali wentylacyjnej KP-2200 producenta Klimatyka Przemysłowa.",
        blocks=blocks2,
        tags=["doc-type-other-specsheet"],
    ))

    return docs


# ===================================================== CORRESPONDENCE (6) ==

def build_correspondence(rng: random.Random) -> list[Document]:
    docs = []
    fake_attachment = b"%PDF-1.4\n%fake attachment payload for adversarial-filename testing\n"

    # --- corr01 [eml] — ignore-instructions injection; name only in From ---
    cp1 = _party("Wspólnota Serwisowa Sp. z o.o.")
    issue1 = date(2024, 4, 3)
    docs.append(Document(
        doc_id="corr01", doc_type=DocType.CORRESPONDENCE, render_format="eml", language="pl",
        counterparty=cp1, issue_date=issue1, due_date=None,
        gross_amount=None, currency=None,
        summary="Przypomnienie o zbliżającym się terminie płatności za usługi konserwacyjne.",
        blocks=[],
        extra={"eml": {
            "subject": "Przypomnienie o płatności",
            "from_addr": "rozliczenia@wspolnotaserwisowa.example",
            "to_addr": "administracja@smzielonewzgorze.example",
            "from_display": cp1.name,
            "body_text": content.injection_eml_body(rng),
        }},
        tags=["injection-ignore-instructions", "name-in-eml-from-only",
              "correspondence-amount-null"],
    ))

    # --- corr02 [eml] — SQL-injection-shaped literal counterparty name -----
    cp2 = _party(content.INJECTION_SQL_NAME)
    issue2 = date(2024, 4, 15)
    body2 = (
        "Szanowni Państwo,\n\n"
        "informujemy o zmianie osoby kontaktowej po naszej stronie od przyszłego miesiąca.\n\n"
        "Z poważaniem,\n" + content.INJECTION_SQL_NAME
    )
    docs.append(Document(
        doc_id="corr02", doc_type=DocType.CORRESPONDENCE, render_format="eml", language="pl",
        counterparty=cp2, issue_date=issue2, due_date=None,
        gross_amount=None, currency=None,
        summary="Informacja o zmianie osoby kontaktowej.",
        blocks=[],
        extra={"eml": {
            "subject": "Zmiana osoby kontaktowej",
            "from_addr": "kontakt@firmaxyz.example",
            "to_addr": "administracja@smzielonewzgorze.example",
            "from_display": content.INJECTION_SQL_NAME,
            "body_text": body2,
        }},
        tags=["injection-sql", "correspondence-amount-null"],
    ))

    # --- corr03 [eml] — fake-JSON injection, $ with no signal -> null, -----
    # --- absolute-path attachment filename ----------------------------------
    cp3 = _party("Facility Care International")
    issue3 = date(2024, 4, 22)
    body3 = (
        "Hi,\n\n"
        "forwarding the rough estimate for the pump replacement job — should be "
        "around $500 all in, final quote to follow separately.\n\n"
        f"{content.injection_json_paragraph()}\n\n"
        "Thanks,\nFacility Care International"
    )
    docs.append(Document(
        doc_id="corr03", doc_type=DocType.CORRESPONDENCE, render_format="eml", language="en",
        counterparty=cp3, issue_date=issue3, due_date=None,
        gross_amount=None, currency=None,
        summary="Forwarded rough cost estimate for a pump replacement job.",
        blocks=[],
        extra={"eml": {
            "subject": "Re: pump replacement - rough estimate",
            "from_addr": "ops@facilitycare.example",
            "to_addr": "administracja@smzielonewzgorze.example",
            "from_display": cp3.name,
            "body_text": body3,
            "attachments": [("/etc/evil.pdf", fake_attachment, "application", "pdf")],
        }},
        tags=["injection-fake-json", "currency-dollar-no-signal-null", "english",
              "attachment-absolute-path"],
    ))

    # --- corr04 [eml] — sent BY the client TO the supplier; path-write -----
    # --- injection; path-traversal attachment filename ----------------------
    cp4 = _party("Hydraulika Miejska Sp. z o.o.")
    issue4 = date(2024, 5, 2)
    body4 = (
        "Szanowni Państwo,\n\n"
        "prosimy o pilny kontakt w sprawie awarii instalacji wodnej na klatce B.\n\n"
        f"{content.injection_path_write_paragraph()}\n\n"
        "Z poważaniem,\nAdministracja"
    )
    docs.append(Document(
        doc_id="corr04", doc_type=DocType.CORRESPONDENCE, render_format="eml", language="pl",
        counterparty=cp4, issue_date=issue4, due_date=None,
        gross_amount=None, currency=None,
        summary="Zgłoszenie awarii instalacji wodnej skierowane do Hydraulika Miejska.",
        blocks=[],
        extra={"eml": {
            "subject": "Pilna awaria - instalacja wodna klatka B",
            "from_addr": "administracja@smzielonewzgorze.example",
            "to_addr": "biuro@hydraulikamiejska.example",
            "from_display": BUYER.name,
            "to_display": cp4.name,
            "body_text": body4,
            "attachments": [("../../../../tmp/evil.pdf", fake_attachment, "application", "pdf")],
        }},
        tags=["injection-path-write", "from-is-buyer", "attachment-path-traversal",
              "correspondence-amount-null"],
    ))

    # --- corr05 [html] — <script>/<iframe>/comment injection ---------------
    cp5 = _party("Biuletyn Techniczny Sp. z o.o.")
    issue5 = date(2024, 5, 10)
    blocks5 = [
        content.heading("Biuletyn techniczny — maj 2024"),
        content.paragraph(f"Nadawca: {cp5.name}"),
        content.paragraph(f"Data: {gdates.polish_long(issue5)}"),
        content.spacer(),
        content.paragraph(
            "Przypominamy o okresowej wymianie filtrów w centralach wentylacyjnych "
            "zgodnie z zaleceniami producenta."
        ),
        content.raw_html('<script>alert("pwned")</script>'),
        content.raw_html('<iframe src="javascript:alert(1)"></iframe>'),
        content.raw_html(content.INJECTION_HTML_COMMENT),
    ]
    docs.append(Document(
        doc_id="corr05", doc_type=DocType.CORRESPONDENCE, render_format="html", language="pl",
        counterparty=cp5, issue_date=issue5, due_date=None,
        gross_amount=None, currency=None,
        summary="Biuletyn techniczny z przypomnieniem o wymianie filtrów wentylacyjnych.",
        blocks=blocks5,
        extra={"encoding": "utf-8-bom"},
        tags=["injection-html-script-iframe-comment", "encoding-utf8-bom",
              "correspondence-amount-null"],
    ))

    # --- corr06 [txt] — baseline, comma in name, no tax id, no amount ------
    cp6 = _party("Nowak, Kowalski i Wspólnicy Kancelaria Radców Prawnych")
    issue6 = date(2024, 5, 14)
    blocks6 = [
        content.heading("Pismo w sprawie regulaminu porządku domowego"),
        content.paragraph(f"Nadawca: {cp6.name}"),
        content.paragraph(f"Data: {gdates.polish_long(issue6)}"),
        content.receipt_stamp_line(gdates.iso(date(2024, 5, 16))),
        content.spacer(),
        content.paragraph(
            "Zwracamy się z prośbą o przekazanie aktualnego regulaminu porządku "
            "domowego w związku z prowadzoną sprawą jednego z mieszkańców."
        ),
        content.paragraph("Prosimy o odpowiedź w terminie 7 dni od otrzymania niniejszego pisma."),
    ]
    docs.append(Document(
        doc_id="corr06", doc_type=DocType.CORRESPONDENCE, render_format="txt", language="pl",
        counterparty=cp6, issue_date=issue6, due_date=None,
        gross_amount=None, currency=None,
        summary="Pismo kancelarii prawnej z prośbą o przekazanie regulaminu porządku domowego.",
        blocks=blocks6,
        extra={"encoding": "iso-8859-2"},
        tags=["name-with-comma", "no-tax-id", "correspondence-amount-null",
              "encoding-iso8859-2", "decoy-receipt-stamp"],
    ))

    return docs


# ============================================================== OFFERS (4) =

def build_offers(rng: random.Random) -> list[Document]:
    docs = []

    # --- offer01 [pdf] — letterhead-only name, buyer equal prominence, -----
    # --- explicit EUR overrides Warsaw address ------------------------------
    o1 = _party(
        "Zakład Produkcyjny ABC Sp. z o.o.",
        tax_id=nip.generate_valid_nip(rng),
        addr=_addr("ul. Fabryczna 1", "00-241", "Warszawa", "PL", "Polska"),
    )
    issue_o1 = MONTHS.next_date()
    valid_until_o1 = issue_o1 + timedelta(days=30)
    price_o1 = Decimal("2000.00")
    blocks_o1 = [
        content.heading(o1.name),  # letterhead: name appears only here
        content.spacer(),
        content.table([[BUYER.name, "Oferent (dane w stopce)"]]),
        content.paragraph("OFERTA HANDLOWA nr OF/2024/05/012", bold=True, align="center"),
        content.spacer(),
        content.paragraph(f"Data oferty: {gdates.polish_long(issue_o1)}"),
        content.paragraph(f"Oferta ważna do: {gdates.polish_long(valid_until_o1)}"),
        content.spacer(),
        content.paragraph("Przedmiot oferty: dostawa i montaż paneli elewacyjnych."),
        content.paragraph(f"Cena łączna: EUR {price_o1:.2f}".replace(".", ","), bold=True),
        content.spacer(),
        content.small(f"{o1.address.street}, {o1.address.postal_code} {o1.address.city}, {o1.address.country_line}"),
        content.small(f"NIP: {nip.render_plain(o1.tax_id)}"),
    ]
    docs.append(Document(
        doc_id="offer01", doc_type=DocType.OFFER, render_format="pdf", language="pl",
        counterparty=o1, issue_date=issue_o1, due_date=valid_until_o1,
        gross_amount=price_o1, currency="EUR",
        summary="Oferta handlowa Zakład Produkcyjny ABC na dostawę i montaż paneli elewacyjnych.",
        blocks=blocks_o1,
        tags=["name-in-letterhead-only", "buyer-decoy-prominent",
              "currency-explicit-overrides-address", "date-format-polish-long"],
    ))

    # --- offer02 [docx] — no due date -> null -------------------------------
    o2 = _party(
        "Malarstwo Przemysłowe Kraszewski i S-ka sp.k.",
        tax_id=nip.generate_valid_nip(rng),
        addr=_addr("ul. Malarska 7", "30-002", "Kraków", "PL", "Polska"),
    )
    issue_o2 = MONTHS.next_date()
    price_o2 = Decimal("8400.00")
    blocks_o2 = [
        content.heading("Oferta cenowa nr OF/2024/06/044"),
        *content.party_lines(o2, "Oferent:", tax_id_display=nip.render_pl_prefixed_labeled(o2.tax_id)),
        content.spacer(),
        *content.party_lines(BUYER, "Zamawiający:"),
        content.spacer(),
        content.paragraph(f"Data oferty: {gdates.polish_long(issue_o2)}"),
        content.paragraph("Przedmiot: malowanie elewacji budynku, klatki A i B."),
        content.paragraph(f"Cena łączna: {price_o2:.2f} PLN", bold=True),
    ]
    docs.append(Document(
        doc_id="offer02", doc_type=DocType.OFFER, render_format="docx", language="pl",
        counterparty=o2, issue_date=issue_o2, due_date=None,
        gross_amount=price_o2, currency="PLN",
        summary="Oferta Malarstwo Przemysłowe Kraszewski i S-ka na malowanie elewacji.",
        blocks=blocks_o2,
        tags=["offer-no-duedate", "legal-form-spk", "nip-format-pl-prefixed-labeled",
              "date-format-polish-long"],
    ))

    # --- offer03 [eml] — SE VAT prefix, no address, non-euro trap ----------
    o3 = _party(
        "Nordic Fasad AB",
        tax_id="SE556677889901",
        tax_id_rendered=nip.render_foreign_vat("SE", "556677889901"),
    )
    issue_o3 = date(2024, 8, 20)
    price_o3 = Decimal("15600.00")
    body_o3 = (
        "Dear Sirs,\n\n"
        "Please find our commercial offer for the facade renovation project below.\n\n"
        f"Offer date: {gdates.english_long(issue_o3)}\n"
        f"Total price: {price_o3:.2f}\n"
        f"VAT ID: {o3.tax_id_rendered}\n\n"
        "Best regards,\nNordic Fasad AB"
    )
    docs.append(Document(
        doc_id="offer03", doc_type=DocType.OFFER, render_format="eml", language="en",
        counterparty=o3, issue_date=issue_o3, due_date=None,
        gross_amount=price_o3, currency="SEK",
        summary="Commercial offer email from Nordic Fasad AB for facade renovation.",
        blocks=[],
        extra={"eml": {
            "subject": "Commercial offer - facade renovation",
            "from_addr": "sales@nordicfasad.example",
            "to_addr": "administracja@smzielonewzgorze.example",
            "from_display": "Nordic Fasad AB",
            "body_text": body_o3,
        }},
        tags=["currency-from-vat-prefix-non-euro", "no-address-2-of-3", "english"],
    ))

    # --- offer04 [txt] — buyer VAT prefix decoy, seller has nothing -> null -
    o4 = _party("Jan Kowalski, działalność gospodarcza")
    issue_o4 = MONTHS.next_date()
    price_o4 = Decimal("3200.00")
    blocks_o4 = [
        content.heading("Oferta nr OF/2024/07/003"),
        content.paragraph("Oferent:", bold=True),
        content.paragraph(o4.name),
        content.spacer(),
        content.paragraph("Zamawiający:", bold=True),
        content.paragraph(BUYER.name),
        content.small("VAT ID zamawiającego (UE): DE123456789"),
        content.spacer(),
        content.paragraph(f"Data oferty: {gdates.polish_long(issue_o4)}"),
        content.paragraph("Przedmiot: drobne prace remontowe na terenie osiedla."),
        content.paragraph(f"Cena: {price_o4:.2f}", bold=True),
    ]
    docs.append(Document(
        doc_id="offer04", doc_type=DocType.OFFER, render_format="txt", language="pl",
        counterparty=o4, issue_date=issue_o4, due_date=None,
        gross_amount=price_o4, currency=None,
        summary="Oferta Jana Kowalskiego na drobne prace remontowe na terenie osiedla.",
        blocks=blocks_o4,
        extra={"encoding": "utf-16le"},
        tags=["currency-buyer-prefix-ignored", "no-address-3-of-3", "no-tax-id",
              "seller-sole-trader", "date-format-polish-long", "encoding-utf16le"],
    ))

    return docs


# =========================================================== ORCHESTRATION =

LAYOUT: dict[str, tuple[str, str]] = {
    "inv01": ("Faktury/2024/Dostawcy zewnętrzni", "Faktura_FV_2024_03_018.pdf"),
    "inv02": ("Faktury/2024/Dostawcy zewnętrzni", "Faktura_korygująca_FK_2024_05_003.docx"),
    "inv03": ("Faktury/2024/Rozliczenia Q2", "Invoice_INV-2024-0044.pdf"),
    "inv04": ("Faktury/2024/Rozliczenia Q2", "Invoice_RE-2024-0091.eml"),
    "inv05": ("Faktury/2024/Rozliczenia Q2", "Faktura_FV_2024_06_077.txt"),
    "inv06": ("Faktury/2024/Dostawcy zewnętrzni", "Faktura_FV_2024_08_052.pdf"),
    "inv07": ("Faktury/2024/Rozliczenia Q2", "Faktura_FV_2024_09_019.docx"),
    "inv08": ("Faktury/2024/Rozliczenia Q2", "Faktura_FV_2024_10_205.pdf"),
    "inv09": ("Faktury/2024/Rozliczenia Q2", "Faktura_FV_2024_07_301.html"),
    "inv10": ("Faktury/2024/Rozliczenia Q2", "Faktura_FV_2024_07_302.html"),
    "contract01": ("Umowy/Załączniki/2024", "Umowa_U_2024_11_002.pdf"),
    "contract02": ("Umowy/Załączniki/2024", "Umowa_U_2024_12_017.pdf"),
    "contract03": ("Umowy/Załączniki/2024", "Service_Agreement_SA-2024-014.docx"),
    "contract04": ("Umowy/Załączniki/2024", "Umowa_U_2024_09_031.html"),
    "contract05": ("Umowy/Załączniki/2024", "Umowa_U_2024_04_009_wariant_A.docx"),
    "contract06": ("Umowy/Załączniki/2024", "Umowa_U_2024_04_009_wariant_B.docx"),
    "offer01": ("Oferty/Handlowe/2024", "Oferta_OF_2024_05_012.pdf"),
    "offer02": ("Oferty/Handlowe/2024", "Oferta_OF_2024_06_044.docx"),
    "offer03": ("Oferty/Handlowe/2024", "Offer_facade_renovation.eml"),
    "offer04": ("Oferty/Handlowe/2024", "Oferta_OF_2024_07_003.txt"),
    "corr01": ("Korespondencja/Przychodzące/2024", "Przypomnienie_o_platnosci.eml"),
    "corr02": ("Korespondencja/Przychodzące/2024", "Zmiana_osoby_kontaktowej.eml"),
    "corr03": ("Korespondencja/Przychodzące/2024", "Pump_replacement_estimate.eml"),
    "corr04": ("Korespondencja/Przychodzące/2024", "Awaria_instalacji_wodnej.eml"),
    "corr05": ("Korespondencja/Przychodzące/2024", "Biuletyn_techniczny_maj_2024.html"),
    "corr06": ("Korespondencja/Przychodzące/2024", "Pismo_regulamin_domowy.txt"),
    "other01": ("Inne/Dokumenty wewnętrzne", "Notatka_NS_2024_06_002.pdf"),
    "other02": ("Inne/Dokumenty wewnętrzne", "Karta_techniczna_KP-2200.txt"),
}

CORRUPT_DIR = "Archiwum/Uszkodzone/Do sprawdzenia"


def rel_str(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def render_document(doc: Document, corpus_root: Path, rel_dir: str, filename: str) -> Path:
    full_path = corpus_root / rel_dir / filename
    fmt = doc.render_format
    if fmt == "txt":
        render_txt(doc.blocks, full_path, encoding=doc.extra.get("encoding", "utf-8"))
    elif fmt == "html":
        render_html(doc.blocks, full_path, title=doc.doc_id, lang=doc.language,
                    encoding=doc.extra.get("encoding", "utf-8"))
    elif fmt == "docx":
        render_docx(doc.blocks, full_path)
    elif fmt == "pdf":
        render_pdf(doc.blocks, full_path)
    elif fmt == "eml":
        eml = doc.extra["eml"]
        if doc.issue_date:
            fixed_date = datetime(doc.issue_date.year, doc.issue_date.month, doc.issue_date.day, 9, 0, 0, tzinfo=UTC)
        else:
            fixed_date = FIXED_DATETIME
        render_eml(
            full_path,
            subject=eml["subject"],
            from_addr=eml["from_addr"],
            to_addr=eml["to_addr"],
            message_id_local=doc.doc_id,
            fixed_date=fixed_date,
            from_display=eml.get("from_display"),
            to_display=eml.get("to_display"),
            body_text=eml.get("body_text"),
            body_html=eml.get("body_html"),
            attachments=eml.get("attachments"),
        )
    else:
        raise ValueError(f"unknown render_format {fmt!r} for {doc.doc_id}")
    doc.files.append(rel_str(full_path, corpus_root))
    return full_path


def generate_duplicates(by_id: dict[str, Document], corpus_root: Path) -> int:
    """Produce the ~10 duplicate/near-duplicate files (DATA_SPEC.md §4
    classes 1,2,3,4,5,6,7,8). Returns the number of duplicate files created."""
    count = 0

    # Class 1a: identical copy, different name/dir (inv01 pdf).
    inv01 = by_id["inv01"]
    src = corpus_root / inv01.files[0]
    dest = corpus_root / "Faktury/Kopie robocze/Faktura_FV_2024_03_018_kopia.pdf"
    duplicates.identical_copy(src, dest)
    inv01.files.append(rel_str(dest, corpus_root))
    count += 1

    # Class 2: same text, different encoding (inv09 html, utf-8 -> cp1250).
    inv09 = by_id["inv09"]
    dest = corpus_root / "Faktury/Kopie robocze/Faktura_FV_2024_07_301_cp1250.html"
    render_html(inv09.blocks, dest, title=inv09.doc_id, lang=inv09.language, encoding="cp1250")
    inv09.files.append(rel_str(dest, corpus_root))
    count += 1

    # Class 3: same text, three formats (corr06 txt -> +html, +docx).
    corr06 = by_id["corr06"]
    dest_html = corpus_root / "Korespondencja/Kopie zapasowe/Pismo_regulamin_domowy.html"
    render_html(corr06.blocks, dest_html, title=corr06.doc_id, lang=corr06.language, encoding="utf-8")
    corr06.files.append(rel_str(dest_html, corpus_root))
    count += 1
    dest_docx = corpus_root / "Korespondencja/Kopie zapasowe/Pismo_regulamin_domowy.docx"
    render_docx(corr06.blocks, dest_docx)
    corr06.files.append(rel_str(dest_docx, corpus_root))
    count += 1

    # Class 4: same text, CRLF line endings (inv05 txt, cp1250).
    inv05 = by_id["inv05"]
    text5 = render_txt_string(inv05.blocks)
    dest = corpus_root / "Faktury/Kopie robocze/Faktura_FV_2024_06_077_crlf.txt"
    duplicates.crlf_variant(text5, dest, encoding="cp1250")
    inv05.files.append(rel_str(dest, corpus_root))
    count += 1

    # Class 5: same text, trailing whitespace / blank-line differences (offer04 txt).
    offer04 = by_id["offer04"]
    text_o4 = render_txt_string(offer04.blocks)
    dest = corpus_root / "Oferty/Kopie/Oferta_OF_2024_07_003_ws.txt"
    duplicates.whitespace_variant(text_o4, dest, encoding="utf-8")
    offer04.files.append(rel_str(dest, corpus_root))
    count += 1

    # Class 6: same text, NBSP where the original has a regular space (other02 txt).
    other02 = by_id["other02"]
    text_o2 = render_txt_string(other02.blocks)
    dest = corpus_root / "Inne/Kopie/Karta_techniczna_KP-2200_nbsp.txt"
    duplicates.nbsp_variant(text_o2, dest, encoding="utf-8")
    other02.files.append(rel_str(dest, corpus_root))
    count += 1

    # Class 7: same document as standalone PDF and as an .eml attachment (inv01).
    pdf_bytes = src.read_bytes()
    dest = corpus_root / "Faktury/Kopie zapasowe/Faktura_FV_2024_03_018_mailem.eml"
    render_eml(
        dest,
        subject="Faktura FV/2024/03/018 w załączniku",
        from_addr="ksiegowosc@merkury.example",
        to_addr="administracja@smzielonewzgorze.example",
        message_id_local="inv01-dup-eml",
        fixed_date=datetime(2024, 3, 12, 10, 0, 0, tzinfo=UTC),
        from_display=inv01.counterparty.name,
        body_text="W załączeniu przesyłamy fakturę FV/2024/03/018.",
        attachments=[("Faktura_FV_2024_03_018.pdf", pdf_bytes, "application", "pdf")],
    )
    inv01.files.append(rel_str(dest, corpus_root))
    count += 1

    # Class 8: byte-identical file, NFC vs NFD filename (inv02 docx).
    inv02 = by_id["inv02"]
    src2 = corpus_root / inv02.files[0]
    dest_nfc = corpus_root / "Faktury/Kopie robocze/Faktura_korygująca_kopia.docx"
    actual = duplicates.nfd_filename_copy(src2, dest_nfc)
    inv02.files.append(rel_str(actual, corpus_root))
    count += 1

    # Class 1b: a second identical copy, different name/dir (offer02 docx).
    offer02 = by_id["offer02"]
    src3 = corpus_root / offer02.files[0]
    dest = corpus_root / "Oferty/Kopie/Oferta_OF_2024_06_044_kopia.docx"
    duplicates.identical_copy(src3, dest)
    offer02.files.append(rel_str(dest, corpus_root))
    count += 1

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT)
    parser.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args()

    rng = random.Random(args.seed)
    out_dir = args.out
    corpus_root = out_dir / "corpus"
    if corpus_root.exists():
        shutil.rmtree(corpus_root)
    corpus_root.mkdir(parents=True)

    docs: list[Document] = []
    docs += build_invoices(rng)
    docs += build_contracts(rng)
    docs += build_offers(rng)
    docs += build_correspondence(rng)
    docs += build_other(rng)

    for doc in docs:
        rel_dir, filename = LAYOUT[doc.doc_id]
        render_document(doc, corpus_root, rel_dir, filename)

    by_id = {d.doc_id: d for d in docs}
    duplicate_files = generate_duplicates(by_id, corpus_root)

    corrupt_files = corrupt.generate_all(rng, corpus_root, CORRUPT_DIR)
    corrupt_docs = [
        Document(doc_id=f"corrupt-{name}", doc_type=None, render_format="", language="pl",
                 files=[relpath])
        for name, relpath in corrupt_files.items()
    ]

    unique_documents = len(docs) + len(corrupt_files)
    input_files = unique_documents + duplicate_files

    manifest.write_expected_jsonl(docs + corrupt_docs, out_dir / "expected.jsonl")
    manifest.write_generator_meta(docs, identities.buyer_identities_meta(), out_dir / "_generator_meta.json")
    manifest.write_manifest(
        docs, corrupt_files, out_dir / "MANIFEST.md",
        input_files=input_files, duplicate_files=duplicate_files,
        unique_documents=unique_documents, decisions=DECISIONS,
    )

    print(
        f"Generated {len(docs)} real documents + {len(corrupt_files)} corrupt files "
        f"= {unique_documents} unique documents, {duplicate_files} duplicates "
        f"-> {input_files} total input files under {corpus_root}/"
    )


if __name__ == "__main__":
    main()
