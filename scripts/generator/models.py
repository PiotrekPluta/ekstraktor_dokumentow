"""Ground-truth data model for the synthetic corpus.

A ``Document`` is constructed first, with every target field already decided.
Renderers consume it to produce files; ``expected.jsonl`` is serialised from
the very same records. No file is ever written first and labelled afterwards
(DATA_SPEC.md R0.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class DocType(str, Enum):
    INVOICE = "invoice"
    CONTRACT = "contract"
    OFFER = "offer"
    CORRESPONDENCE = "correspondence"
    OTHER = "other"


@dataclass(frozen=True)
class Address:
    """A postal address. ``country_code`` is always known to the generator
    (it drives currency inference) even when ``country_line`` is omitted from
    the rendered text, to exercise the "address present but country absent"
    case.
    """

    street: str
    postal_code: str
    city: str
    country_code: str  # ISO 3166-1 alpha-2, ground truth
    country_line: str | None  # text actually rendered, or None to omit it


@dataclass(frozen=True)
class Party:
    """A named entity appearing in a document: seller, buyer, or the
    counterparty of a piece of correspondence.
    """

    name: str
    tax_id: str | None = None  # ground-truth normalised id, e.g. "1234567890" or "DE123456789"
    tax_id_rendered: str | None = None  # exact string to print in the document
    address: Address | None = None


@dataclass
class Block:
    """One piece of document content, format-agnostic.

    Renderers (render_txt/html/docx/pdf/eml) turn a ``list[Block]`` into an
    actual file. Not every renderer honours every kind/style combination
    (e.g. ``page_break`` is a no-op outside PDF), which is fine: each
    document is generated for exactly one target format.
    """

    kind: str  # "heading" | "paragraph" | "table" | "spacer" | "page_break" | "raw_html"
    text: str = ""
    rows: list[list[str]] | None = None
    align: str = "left"  # "left" | "right" | "center"
    bold: bool = False
    size: str = "normal"  # "small" | "normal" | "large"
    raw_html: str = ""


@dataclass
class Document:
    """One logical document: ground truth plus everything needed to render it."""

    doc_id: str
    doc_type: DocType | None  # None for corrupt / unrecognisable files
    render_format: str  # "txt" | "html" | "docx" | "pdf" | "eml"
    language: str  # "pl" | "en"

    counterparty: Party | None = None
    issue_date: date | None = None
    due_date: date | None = None
    gross_amount: Decimal | None = None
    currency: str | None = None
    summary: str | None = None

    blocks: list[Block] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    # Populated after rendering + duplicate generation: corpus-relative paths
    # (forward slashes, NFC) of every file representing this document.
    files: list[str] = field(default_factory=list)

    def expected_row(self) -> dict:
        """Serialise to one ``expected.jsonl`` record (DATA_SPEC.md §8)."""
        return {
            "doc_type": self.doc_type.value if self.doc_type else None,
            "counterparty_name": self.counterparty.name if self.counterparty else None,
            "counterparty_tax_id": self.counterparty.tax_id if self.counterparty else None,
            "issue_date": self.issue_date.isoformat() if self.issue_date else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "gross_amount": (f"{self.gross_amount:.2f}" if self.gross_amount is not None else None),
            "currency": self.currency,
            "summary": self.summary or "",
            "files": list(self.files),
        }
