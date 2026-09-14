"""Format detection by magic bytes — never by filename extension.

data/corpus/Archiwum/Uszkodzone/Do sprawdzenia/ contains fixtures that only
make sense under that rule: `logo.pdf` is a real PNG, `umowa.docx` is random
binary, `dane.txt` is random binary too. Trusting the extension on any of
these would silently mis-route them into a parser instead of quarantine.
"""

from __future__ import annotations

import re
from enum import StrEnum

from charset_normalizer import from_bytes

HEAD_SAMPLE_SIZE = 8192
_MAX_CONTROL_CHAR_RATIO = 0.05

_EML_HEADER_RE = re.compile(
    rb"^(From|To|Subject|Date|Message-ID|MIME-Version|Content-Type|Return-Path):",
    re.IGNORECASE | re.MULTILINE,
)
_HTML_MARKER_RE = re.compile(
    rb"<(!doctype\s+html|html[\s>]|head[\s>]|body[\s>])", re.IGNORECASE
)


class Format(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    HTML = "html"
    EML = "eml"
    TXT = "txt"
    UNKNOWN = "unknown"


def detect_format(head: bytes) -> Format:
    """Classify a file from its first bytes (a `HEAD_SAMPLE_SIZE`-byte prefix
    is always enough — none of the checks below need the full file).
    """
    if head.startswith(b"%PDF-"):
        return Format.PDF

    # Docx is a zip; further validation (does it actually contain a docx
    # package, not just any zip) happens in the extractor, not here — that
    # needs to open the central directory, which this head-bytes-only check
    # deliberately avoids so detection stays O(1) even on a huge file.
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return Format.DOCX

    # Checked before HTML: an eml's very first bytes are always headers,
    # never markup, so this is the more specific signal of the two.
    if _looks_like_eml(head):
        return Format.EML

    if _HTML_MARKER_RE.search(head[:1024]):
        return Format.HTML

    if _looks_like_text(head):
        return Format.TXT

    return Format.UNKNOWN


def _looks_like_eml(head: bytes) -> bool:
    first_line = head.split(b"\n", 1)[0]
    if not _EML_HEADER_RE.match(first_line):
        return False
    return len(_EML_HEADER_RE.findall(head[:2048])) >= 2


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return True  # empty file: trivially valid (empty) text
    match = from_bytes(head).best()
    if match is None:
        return False
    decoded = str(match)
    control = sum(1 for ch in decoded if ord(ch) < 32 and ch not in "\t\n\r")
    return (control / len(decoded)) <= _MAX_CONTROL_CHAR_RATIO
