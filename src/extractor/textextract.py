"""Per-format text extraction.

Every function here either returns extracted text or raises
`ExtractionFailed` carrying one of the closed quarantine-reason values from
docs/PROJECT_NOTES.md §8 Stage 3: `corrupt_file`, `unsupported_format`,
`empty_text`, `no_text_layer`. (`parse_timeout` and `llm_invalid_output`
aren't produced here — the former needs the subprocess/timeout hardening
that's explicitly deferred to Stage 3, the latter is a Stage 6 concern.)

No subprocess isolation or timeout yet: a pathological file could hang this
today. That hardening is Stage 3's job, wrapping these same functions.

Reading is capped at MAX_TEXT_CHARS for every format. This exists to bound
memory for the ~300MB fixture (docs/DATA_SPEC.md), not to pick good model
context — Stage 4 replaces this with real head/tail/keyword windowing for
the actual LLM call. A side effect worth stating plainly: two documents
identical only up to the cap would dedup together. Not exercised by the
current fixtures, but a real limitation.
"""

from __future__ import annotations

import email
import io
import re
import zipfile
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from html.parser import HTMLParser

import docx
import pypdfium2 as pdfium

from extractor.formats import HEAD_SAMPLE_SIZE, Format, detect_format
from extractor.normalize import decode_bytes

MAX_TEXT_CHARS = 2_000_000

# Attachment formats an eml is unpacked into text through. Nested eml
# attachments are not followed — a documented limitation, not an oversight.
_EML_ATTACHMENT_FORMATS = {Format.PDF, Format.DOCX, Format.HTML, Format.TXT}

_META_CHARSET_RE = re.compile(rb'charset=["\']?\s*([\w-]+)', re.IGNORECASE)


class ExtractionFailed(Exception):
    """Carries the quarantine reason a failed extraction should be filed
    under — one of the closed set on `documents.quarantine_reason`.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ExtractionResult:
    text: str
    # Matches files.content_source (extractor/db.py): 'own' for every format
    # except an eml whose attachment (not its body) supplied the text.
    content_source: str = "own"


def extract_text(fmt: Format, data: bytes) -> ExtractionResult:
    if fmt is Format.UNKNOWN:
        raise ExtractionFailed(
            "unsupported_format", "format not recognised from magic bytes"
        )
    if fmt is Format.TXT:
        return ExtractionResult(_extract_txt(data))
    if fmt is Format.HTML:
        return ExtractionResult(_extract_html(data))
    if fmt is Format.EML:
        return _extract_eml(data)
    if fmt is Format.PDF:
        return ExtractionResult(_extract_pdf(data))
    if fmt is Format.DOCX:
        return ExtractionResult(_extract_docx(data))
    raise AssertionError(f"unhandled format: {fmt!r}")  # pragma: no cover


def _extract_txt(data: bytes) -> str:
    return decode_bytes(data[:MAX_TEXT_CHARS])


def _extract_html(data: bytes) -> str:
    declared = _sniff_html_charset(data)
    text = decode_bytes(data[:MAX_TEXT_CHARS], declared)
    collector = _HTMLTextCollector()
    collector.feed(text)
    collector.close()
    return "".join(collector.chunks)


def _sniff_html_charset(data: bytes) -> str | None:
    match = _META_CHARSET_RE.search(data[:4096])
    if match is None:
        return None
    return match.group(1).decode("ascii", errors="ignore")


class _HTMLTextCollector(HTMLParser):
    # <title> is browser-chrome metadata, never page content — leaving it
    # in once let a generator-internal id ("corr06") leak into extracted
    # text and silently broke dedup against the same letter's .docx/.txt
    # renderings (docs/DATA_SPEC.md's "small formatting differences" case).
    _SKIPPED_TAGS = frozenset({"script", "style", "title"})

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIPPED_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.chunks.append(data)


def _extract_eml(data: bytes) -> ExtractionResult:
    try:
        msg = email.message_from_bytes(data, policy=policy.default)
    except Exception as exc:
        raise ExtractionFailed("corrupt_file", f"could not parse eml: {exc}") from exc
    if not isinstance(msg, EmailMessage):
        raise ExtractionFailed(
            "corrupt_file", "eml did not parse into a usable message"
        )

    attachment_text = _extract_eml_attachment(msg)
    if attachment_text:
        return ExtractionResult(attachment_text, content_source="eml_attachment")

    body_text = _extract_eml_body(msg)
    if body_text:
        return ExtractionResult(body_text, content_source="own")

    raise ExtractionFailed(
        "empty_text", "eml has neither a usable attachment nor body text"
    )


def _extract_eml_attachment(msg: EmailMessage) -> str | None:
    """Attachment content wins over the covering note when it's real: an
    email forwarding an invoice IS that invoice. The declared filename is
    read only as metadata, never used to build a filesystem path — the
    corpus's `/etc/evil.pdf` / `../../../../tmp/evil.pdf` attachment names
    are neutralised by that alone, no separate path-traversal guard needed.
    """
    try:
        # Deliberately broad: a malformed MIME structure here must fall
        # back to the body, not abort extraction of the whole email.
        attachments = list(msg.iter_attachments())
    except Exception:  # noqa: BLE001
        return None

    for part in attachments:
        try:
            # Same reasoning: one malformed part must not lose the rest.
            payload = part.get_content()
        except Exception:  # noqa: BLE001, S112
            continue
        if not isinstance(payload, (bytes, bytearray)):
            continue
        payload = bytes(payload)
        fmt = detect_format(payload[:HEAD_SAMPLE_SIZE])
        if fmt not in _EML_ATTACHMENT_FORMATS:
            continue
        try:
            result = extract_text(fmt, payload)
        except ExtractionFailed:
            continue
        if result.text.strip():
            return result.text
    return None


def _extract_eml_body(msg: EmailMessage) -> str:
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None:
        return plain.get_content()
    html_part = msg.get_body(preferencelist=("html",))
    if html_part is not None:
        return _extract_html(html_part.get_content().encode("utf-8"))
    return ""


def _extract_pdf(data: bytes) -> str:
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise ExtractionFailed(
            "corrupt_file", f"pdfium could not open document: {exc}"
        ) from exc

    chunks: list[str] = []
    total = 0
    try:
        for page in pdf:
            text = page.get_textpage().get_text_range()
            if text:
                chunks.append(text)
                total += len(text)
            if total >= MAX_TEXT_CHARS:
                break
    except pdfium.PdfiumError as exc:
        raise ExtractionFailed(
            "corrupt_file", f"pdfium failed mid-document: {exc}"
        ) from exc

    combined = "".join(chunks)
    if not combined.strip():
        raise ExtractionFailed(
            "no_text_layer", "PDF has no extractable text (scanned/image-only page?)"
        )
    return combined


def _extract_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile as exc:
        raise ExtractionFailed("corrupt_file", f"not a valid zip: {exc}") from exc

    if "[Content_Types].xml" not in names:
        raise ExtractionFailed("unsupported_format", "zip file is not a docx package")

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        # python-docx raises several different exception types for a broken
        # package (KeyError, PackageNotFoundError, lxml parse errors...) —
        # we already confirmed above it's at least docx-shaped, so any
        # failure to open it past that point means the package is corrupt.
        raise ExtractionFailed(
            "corrupt_file", f"docx package is broken: {exc}"
        ) from exc

    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" ".join(cell.text for cell in row.cells))
    return "\n".join(parts)
