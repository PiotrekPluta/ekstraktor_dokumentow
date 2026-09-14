"""Duplicate and near-duplicate file generation (DATA_SPEC.md §4).

Each function here produces ONE additional file that represents the same
logical document as some already-rendered base file. The two near-duplicate
traps (§4 cases 9 and 10 — same template, one field changed) are NOT here:
they are two independent ``Document`` records in ``generate_data.py``, since
they must hash differently.
"""

from __future__ import annotations

import shutil
import unicodedata
from pathlib import Path


def identical_copy(src: Path, dest: Path) -> None:
    """Class 1: byte-identical file, different name/directory."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest)


def reencoded_text(text: str, dest: Path, encoding: str) -> None:
    """Class 2: same text, different encoding."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if encoding == "utf-8-bom":
        dest.write_bytes(text.encode("utf-8-sig"))
    else:
        dest.write_bytes(text.encode(encoding))


def crlf_variant(text: str, dest: Path, *, encoding: str = "utf-8") -> None:
    """Class 4: same text, CRLF line endings instead of LF."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    converted = text.replace("\r\n", "\n").replace("\n", "\r\n")
    dest.write_bytes(converted.encode(encoding))


def whitespace_variant(text: str, dest: Path, *, encoding: str = "utf-8") -> None:
    """Class 5: same text, differing only in trailing whitespace and blank-line count."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = text.split("\n")
    padded = [line + "   " for line in lines]
    result = "\n\n".join(padded) + "\n\n\n"
    dest.write_bytes(result.encode(encoding))


def nbsp_variant(text: str, dest: Path, *, encoding: str = "utf-8") -> None:
    """Class 6: same text with NBSP (U+00A0) where the original has a regular space."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    converted = text.replace(" ", " ")
    dest.write_bytes(converted.encode(encoding))


def nfd_filename_copy(src: Path, dest_nfc: Path) -> Path:
    """Class 8: byte-identical file whose filename differs only by Unicode
    normalisation of its diacritics (NFC vs NFD) — what macOS hands the
    tool from a directory listing. Returns the actual NFD path written."""
    dest_nfc.parent.mkdir(parents=True, exist_ok=True)
    nfd_name = unicodedata.normalize("NFD", dest_nfc.name)
    dest_nfd = dest_nfc.parent / nfd_name
    shutil.copy(src, dest_nfd)
    return dest_nfd
