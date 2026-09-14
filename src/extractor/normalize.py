"""Byte decoding and text normalisation shared by every format extractor.

Encoding resolution order (docs/PROJECT_NOTES.md §8 Stage 3): BOM -> declared
charset (mail header / HTML meta) -> charset-normalizer. CP1250 and
ISO-8859-2 differ exactly on the Polish letters (a/s/z with accents), which is
exactly the kind of thing a confidence-based guesser can get wrong, so an
explicit declared charset always wins when one is available.

normalize_text() also case-folds and drops decoration-only lines (a line of
nothing but repeated `=`/`-`/`_`/`*`/`~`/`#`). Both are deliberately narrow:
they exist because the txt renderer in this corpus uppercases every heading
and underlines it (scripts/generator/render_txt.py), which is a realistic,
general plain-text convention, not a one-off — the same letter rendered to
.docx/.html/.txt must still dedup to one document (docs/ZADANIE.md: "small
formatting differences"). Neither goes further into fuzzy content matching;
case and pure decoration carry no semantic content in a business document.
"""

from __future__ import annotations

import re
import unicodedata

from charset_normalizer import from_bytes

_WHITESPACE_RE = re.compile(r"\s+")
_DIVIDER_LINE_RE = re.compile(r"^[=\-_*~#]{3,}$")

_BOMS: tuple[tuple[bytes, str], ...] = (
    # "utf-16"/"utf-32" (not the -le/-be variants) both auto-detect
    # endianness from the BOM AND strip it; the explicit-endian codecs
    # decode the BOM bytes as a literal U+FEFF instead of consuming them.
    # 4-byte utf-32 BOMs must be checked first: the utf-32-le BOM starts
    # with the same two bytes as the utf-16-le BOM.
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)


def decode_bytes(data: bytes, declared_charset: str | None = None) -> str:
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return data.decode(encoding, errors="replace")

    if declared_charset:
        try:
            return data.decode(declared_charset, errors="strict")
        except (LookupError, UnicodeDecodeError):
            pass

    match = from_bytes(data).best()
    if match is not None:
        return str(match)

    return data.decode("utf-8", errors="replace")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    lines = (
        line for line in text.splitlines() if not _DIVIDER_LINE_RE.match(line.strip())
    )
    text = "\n".join(lines)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text.casefold()
