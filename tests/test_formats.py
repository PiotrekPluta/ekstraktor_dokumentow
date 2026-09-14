"""Format detection — magic bytes only, never the filename extension.

The corrupt/ fixtures exist precisely to test that: `logo.pdf` is a real
PNG, `umowa.docx` is random binary, `dane.txt` is random binary too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from extractor.formats import HEAD_SAMPLE_SIZE, Format, detect_format

REPO_ROOT = Path(__file__).resolve().parent.parent
CORRUPT_DIR = (
    REPO_ROOT / "data" / "corpus" / "Archiwum" / "Uszkodzone" / "Do sprawdzenia"
)


def _detect_file(path: Path) -> Format:
    with path.open("rb") as fh:
        head = fh.read(HEAD_SAMPLE_SIZE)
    return detect_format(head)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("dane.txt", Format.UNKNOWN),  # random binary, despite the .txt name
        ("logo.pdf", Format.UNKNOWN),  # a real PNG, despite the .pdf name
        ("oferta.docx", Format.DOCX),  # zip-shaped, truncated docx package
        ("pusty.txt", Format.TXT),  # empty file
        ("raport_uciety.pdf", Format.PDF),  # valid header, truncated body
        ("skan_umowy.pdf", Format.PDF),  # valid, scanned/image-only
        ("umowa.docx", Format.UNKNOWN),  # random binary, not even a zip
        ("zabezpieczona.pdf", Format.PDF),  # valid, password-protected
    ],
)
def test_corrupt_fixtures_detected_by_magic_bytes(
    filename: str, expected: Format
) -> None:
    assert _detect_file(CORRUPT_DIR / filename) == expected


@pytest.mark.parametrize(
    ("relpath", "expected"),
    [
        ("Faktury/2024/Rozliczenia Q2/Invoice_RE-2024-0091.eml", Format.EML),
        ("Faktury/2024/Rozliczenia Q2/Faktura_FV_2024_07_301.html", Format.HTML),
        ("Faktury/2024/Dostawcy zewnętrzni/Faktura_FV_2024_03_018.pdf", Format.PDF),
        (
            "Faktury/2024/Dostawcy zewnętrzni/Faktura_korygująca_FK_2024_05_003.docx",
            Format.DOCX,
        ),
        ("Faktury/2024/Rozliczenia Q2/Faktura_FV_2024_06_077.txt", Format.TXT),
        ("Faktury/Kopie robocze/Faktura_FV_2024_07_301_cp1250.html", Format.HTML),
    ],
)
def test_real_documents_detected_by_magic_bytes(relpath: str, expected: Format) -> None:
    assert _detect_file(REPO_ROOT / "data" / "corpus" / relpath) == expected


def test_empty_bytes_is_txt() -> None:
    assert detect_format(b"") == Format.TXT


def test_random_binary_is_unknown() -> None:
    assert detect_format(bytes(range(256)) * 4) == Format.UNKNOWN


def test_pdf_signature() -> None:
    assert detect_format(b"%PDF-1.7\nrest of file") == Format.PDF


def test_zip_signature_is_docx() -> None:
    assert detect_format(b"PK\x03\x04\x14\x00\x00\x00rest") == Format.DOCX


def test_html_doctype() -> None:
    assert (
        detect_format(b"<!DOCTYPE html>\n<html><body>hi</body></html>") == Format.HTML
    )


def test_eml_needs_at_least_two_header_lines() -> None:
    single_header = (
        b"From: someone@example.com\nJust a stray colon: not really a header block\n"
    )
    assert detect_format(single_header) != Format.EML

    real_headers = b"From: a@example.com\nSubject: hi\nDate: Mon, 1 Jan 2026 00:00:00 +0000\n\nbody"
    assert detect_format(real_headers) == Format.EML
