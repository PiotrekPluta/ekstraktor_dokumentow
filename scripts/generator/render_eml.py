"""EML rendering via stdlib ``email``, with fixed Date/Message-ID for
reproducibility (DATA_SPEC.md R0.3) and support for adversarial attachment
filenames (DATA_SPEC.md §5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from pathlib import Path


def render_eml(
    path: Path,
    *,
    subject: str,
    from_addr: str,
    to_addr: str,
    message_id_local: str,
    fixed_date: datetime,
    from_display: str | None = None,
    to_display: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    attachments: list[tuple[str, bytes, str, str]] | None = None,
) -> None:
    """``attachments``: list of (filename, content, maintype, subtype)."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _format_address(from_display, from_addr)
    msg["To"] = _format_address(to_display, to_addr)
    msg["Date"] = format_datetime(fixed_date.astimezone(UTC))
    msg["Message-ID"] = f"<{message_id_local}@corpus.local>"

    if body_html and not body_text:
        msg.set_content("Ta wiadomość wymaga klienta obsługującego HTML.")
        msg.add_alternative(body_html, subtype="html")
    else:
        msg.set_content(body_text or "")
        if body_html:
            msg.add_alternative(body_html, subtype="html")

    for filename, content, maintype, subtype in attachments or []:
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    # EmailMessage assigns a random MIME boundary per multipart part on each
    # call unless told otherwise, which breaks R0.3 byte-reproducibility.
    boundary_index = 0
    for part in msg.walk():
        if part.is_multipart():
            part.set_boundary(f"boundary-{message_id_local}-{boundary_index}")
            boundary_index += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(msg))


def _format_address(display: str | None, addr: str) -> str:
    if display:
        return formataddr((display, addr))
    return addr
