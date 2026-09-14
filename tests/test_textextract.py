"""Per-format extraction, exercised mostly against real corpus fixtures —
the quarantine-reason mapping was verified by hand against these exact
files (docs/PROJECT_NOTES.md's design notes), so that mapping is what's
pinned down here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from extractor.formats import HEAD_SAMPLE_SIZE, Format, detect_format
from extractor.textextract import ExtractionFailed, extract_text

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "data" / "corpus"
CORRUPT_DIR = CORPUS / "Archiwum" / "Uszkodzone" / "Do sprawdzenia"


def _extract_file(path: Path):
    data = path.read_bytes()
    fmt = detect_format(data[:HEAD_SAMPLE_SIZE])
    return extract_text(fmt, data)


@pytest.mark.parametrize(
    ("filename", "reason"),
    [
        ("dane.txt", "unsupported_format"),
        ("logo.pdf", "unsupported_format"),
        ("oferta.docx", "corrupt_file"),
        ("raport_uciety.pdf", "corrupt_file"),
        ("skan_umowy.pdf", "no_text_layer"),
        ("umowa.docx", "unsupported_format"),
        ("zabezpieczona.pdf", "corrupt_file"),
    ],
)
def test_corrupt_fixtures_fail_with_expected_reason(filename: str, reason: str) -> None:
    with pytest.raises(ExtractionFailed) as exc_info:
        _extract_file(CORRUPT_DIR / filename)
    assert exc_info.value.reason == reason


def test_empty_file_extracts_to_empty_string() -> None:
    # pusty.txt is 0 bytes: extraction itself succeeds (a valid, empty
    # text), quarantine as empty_text is inventory.py's job (it's the one
    # that decides "empty after normalization" is unprocessable).
    result = _extract_file(CORRUPT_DIR / "pusty.txt")
    assert result.text == ""
    assert result.content_source == "own"


def test_unsupported_format_never_attempts_extraction() -> None:
    with pytest.raises(ExtractionFailed) as exc_info:
        extract_text(Format.UNKNOWN, b"whatever")
    assert exc_info.value.reason == "unsupported_format"


def test_real_pdf_extracts_nonempty_text() -> None:
    result = _extract_file(
        CORPUS / "Faktury/2024/Dostawcy zewnętrzni/Faktura_FV_2024_03_018.pdf"
    )
    assert "FV" in result.text or "Faktura" in result.text
    assert result.content_source == "own"


def test_real_docx_extracts_nonempty_text() -> None:
    result = _extract_file(
        CORPUS
        / "Faktury/2024/Dostawcy zewnętrzni/Faktura_korygująca_FK_2024_05_003.docx"
    )
    assert result.text.strip()
    assert result.content_source == "own"


def test_html_strips_title_script_and_style() -> None:
    html = (
        b"<!DOCTYPE html><html><head><title>internal-id-42</title>"
        b"<style>body{color:red}</style></head>"
        b"<body><script>alert(1)</script><p>Widoczny tekst</p></body></html>"
    )
    result = extract_text(Format.HTML, html)
    assert "Widoczny tekst" in result.text
    assert "internal-id-42" not in result.text
    assert "alert" not in result.text
    assert "color:red" not in result.text


def test_eml_with_real_attachment_uses_attachment_text() -> None:
    result = _extract_file(
        CORPUS / "Faktury/Kopie zapasowe/Faktura_FV_2024_03_018_mailem.eml"
    )
    standalone = _extract_file(
        CORPUS / "Faktury/2024/Dostawcy zewnętrzni/Faktura_FV_2024_03_018.pdf"
    )
    assert result.content_source == "eml_attachment"
    assert result.text == standalone.text


def test_eml_with_fake_attachment_falls_back_to_body() -> None:
    # The attachment here is a 67-byte stub under a path-traversal filename
    # (/etc/evil.pdf) — not real PDF content, so extraction of it fails and
    # this must fall back to the (real, correspondence) body text.
    result = _extract_file(
        CORPUS / "Korespondencja/Przychodzące/2024/Awaria_instalacji_wodnej.eml"
    )
    assert result.content_source == "own"
    assert "awari" in result.text.lower()


def test_eml_without_attachment_uses_body() -> None:
    result = _extract_file(
        CORPUS / "Faktury/2024/Rozliczenia Q2/Invoice_RE-2024-0091.eml"
    )
    assert result.content_source == "own"
    assert result.text.strip()


def test_eml_attachment_filename_never_touches_the_filesystem(tmp_path: Path) -> None:
    """The two evil-attachment fixtures carry filenames like /etc/evil.pdf
    and ../../../../tmp/evil.pdf. Extraction must not create, read, or
    otherwise touch any path built from that filename — the guarantee is
    structural (the declared filename is never used for I/O), not a
    sanitisation step, so this just confirms nothing appeared on disk.
    """
    before = set(tmp_path.iterdir())
    _extract_file(
        CORPUS / "Korespondencja/Przychodzące/2024/Pump_replacement_estimate.eml"
    )
    assert set(tmp_path.iterdir()) == before
    assert not Path("/etc/evil.pdf").exists()
