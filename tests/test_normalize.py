from __future__ import annotations

import unicodedata

from extractor.normalize import decode_bytes, normalize_text


def test_decode_utf8_bom_stripped() -> None:
    assert decode_bytes("zażółć".encode("utf-8-sig")) == "zażółć"


def test_decode_utf16_le_bom() -> None:
    assert decode_bytes(b"\xff\xfeh\x00i\x00") == "hi"


def test_decode_utf16_be_bom() -> None:
    assert decode_bytes(b"\xfe\xff\x00h\x00i") == "hi"


def test_declared_charset_is_honoured() -> None:
    # cp1250 and iso-8859-2 agree on most bytes but diverge on some Polish
    # letters (docs/PROJECT_NOTES.md §8 Stage 3) — a byte sequence that
    # decodes to something else entirely under the wrong one of the two
    # must still come out right when the charset is declared explicitly.
    data = "kwota brutto: 123,45 zł, NIP: 526-000-12-46".encode("cp1250")
    assert decode_bytes(data, declared_charset="cp1250") == (
        "kwota brutto: 123,45 zł, NIP: 526-000-12-46"
    )


def test_declared_charset_invalid_falls_back_to_guessing() -> None:
    data = "kwota brutto 123,45 zł".encode()
    assert (
        decode_bytes(data, declared_charset="not-a-real-charset")
        == "kwota brutto 123,45 zł"
    )


def test_decode_without_hints_uses_charset_normalizer() -> None:
    # UTF-8's multi-byte sequences are close to self-validating, so a
    # confidence-based guesser resolves this reliably with no hints at all.
    text = "Faktura VAT nr FV/2024/07/301. Sprzedawca: Zakład Usługowy. Kwota brutto: 861,00 zł."
    data = text.encode("utf-8")
    assert decode_bytes(data) == text


def test_declared_charset_honoured_for_iso8859_2_too() -> None:
    # cp1250 and iso-8859-2 diverge only on a handful of Polish letters
    # (docs/PROJECT_NOTES.md §8 Stage 3), and how reliably a confidence-based
    # guesser tells them apart depends on how much diacritic signal happens
    # to be in a given sample — which is exactly why a declared charset must
    # be honoured outright rather than left to a guess in the first place.
    text = "Zakład Usługowy, ul. Żółwia, NIP: 526-000-12-46"
    data = text.encode("iso-8859-2")
    assert decode_bytes(data, declared_charset="iso-8859-2") == text


def test_normalize_nfc_composes_combining_characters() -> None:
    decomposed = unicodedata.normalize("NFD", "żółw")  # split accents back out
    normalized = normalize_text(decomposed)
    assert normalized == unicodedata.normalize("NFC", decomposed).casefold()
    assert len(normalized) < len(
        decomposed
    )  # combining pairs collapsed to single codepoints


def test_normalize_collapses_whitespace() -> None:
    assert normalize_text("a   b\n\nc\t\td") == "a b c d"


def test_normalize_strips_decorative_divider_lines() -> None:
    text = "PISMO\n=====\n\nbody text here"
    assert normalize_text(text) == "pismo body text here"


def test_normalize_keeps_short_dash_runs_that_arent_pure_dividers() -> None:
    # "==" (2 chars) is below the 3-char divider threshold — must survive.
    assert "==" in normalize_text("x == y")


def test_normalize_casefolds() -> None:
    assert normalize_text("PISMO Regulaminu") == normalize_text("pismo regulaminu")


def test_normalize_empty_string() -> None:
    assert normalize_text("") == ""
    assert normalize_text("   \n\t  ") == ""
