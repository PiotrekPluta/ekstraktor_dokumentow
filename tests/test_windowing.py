from __future__ import annotations

from pathlib import Path

from extractor.formats import HEAD_SAMPLE_SIZE, detect_format
from extractor.textextract import extract_text
from extractor.windowing import (
    DEFAULT_CHAR_BUDGET,
    build_context,
    clean_for_context,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "data" / "corpus"


def test_short_text_passes_through_unchanged() -> None:
    text = (
        "Faktura VAT nr FV/2024/03/018. Sprzedawca: Test Sp. z o.o. "
        "NIP: 5260001246. Kwota brutto: 861,00 zł."
    )
    assert build_context(text) == clean_for_context(text)


def test_clean_for_context_does_not_casefold_or_strip_dividers() -> None:
    # Unlike extractor/normalize.py's normalize_text() (dedup-purposed):
    # this text is read, not hashed, so case and structure should survive.
    text = "PISMO\n=====\n\nTreść pisma tutaj."
    cleaned = clean_for_context(text)
    assert "PISMO" in cleaned
    assert "=====" in cleaned


def test_clean_for_context_collapses_whitespace_and_composes_nfc() -> None:
    import unicodedata

    decomposed = unicodedata.normalize("NFD", "Zażółć") + "  gęślą   jaźń"
    cleaned = clean_for_context(decomposed)
    assert cleaned == unicodedata.normalize("NFC", cleaned)
    assert "   " not in cleaned


def test_long_text_is_windowed_within_budget() -> None:
    filler = "Lorem ipsum dolor sit amet consectetur. " * 2000
    windowed = build_context(filler, char_budget=1000)
    assert len(windowed) <= 1500  # budget plus headroom for the omission markers


def test_head_and_tail_are_preserved() -> None:
    head_marker = "HEAD_MARKER_TEXT"
    tail_marker = "TAIL_MARKER_TEXT"
    filler = "x" * 20000
    text = head_marker + filler + tail_marker
    windowed = build_context(text, char_budget=3000)
    assert head_marker in windowed
    assert tail_marker in windowed


def test_keyword_hit_in_the_middle_survives_windowing() -> None:
    """The real point of Stage 4: a field mentioned nowhere near the head
    or tail of a long document must still reach the model.
    """
    filler = "Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 1000
    marker = " MIDDLE_SECRET NIP: 526-000-12-46 brutto: 9999,99 zł here. "
    text = filler[:20000] + marker + filler[20000:]
    assert (
        20000 < text.index("MIDDLE_SECRET") < len(text) - 5000
    )  # genuinely mid-document

    windowed = build_context(text, char_budget=6000)
    assert "MIDDLE_SECRET" in windowed
    assert "526-000-12-46" in windowed


def test_omitted_content_is_marked_not_silently_dropped() -> None:
    filler = "Lorem ipsum dolor sit amet consectetur. " * 2000
    windowed = build_context(filler, char_budget=1000)
    assert "[...]" in windowed


def test_real_long_pdf_nip_survives_windowing() -> None:
    """Umowa_U_2024_11_002.pdf: 286 pages, with a NIP that appears only
    around page 280 and nowhere else (docs/DATA_SPEC.md §3.1) — exactly
    the "regardless of where in the document" case Stage 4 exists for.
    """
    path = CORPUS / "Umowy/Załączniki/2024/Umowa_U_2024_11_002.pdf"
    data = path.read_bytes()
    fmt = detect_format(data[:HEAD_SAMPLE_SIZE])
    text = extract_text(fmt, data).text

    windowed = build_context(text)
    assert len(windowed) < len(clean_for_context(text))  # confirms windowing kicked in
    assert "6904020943" in windowed.replace("-", "").replace(" ", "")


def test_default_budget_is_documented_and_positive() -> None:
    assert DEFAULT_CHAR_BUDGET > 0
