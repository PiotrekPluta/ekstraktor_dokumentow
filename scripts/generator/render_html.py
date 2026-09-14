"""HTML rendering, including deliberately hostile markup (DATA_SPEC.md §5)."""

from __future__ import annotations

import html as html_lib
from pathlib import Path

from .models import Block

_CHARSET_LABELS = {
    "utf-8": "UTF-8",
    "utf-8-bom": "UTF-8",
    "cp1250": "windows-1250",
    "iso-8859-2": "ISO-8859-2",
    "utf-16le": "UTF-16LE",
}


def _esc(text: str) -> str:
    return html_lib.escape(text)


def render_html_string(blocks: list[Block], *, title: str, lang: str, charset_label: str) -> str:
    parts = [
        "<!DOCTYPE html>",
        f'<html lang="{lang}">',
        "<head>",
        f'<meta charset="{charset_label}">',
        f"<title>{_esc(title)}</title>",
        "</head>",
        "<body>",
    ]
    for b in blocks:
        if b.kind == "heading":
            parts.append(f"<h2>{_esc(b.text)}</h2>")
        elif b.kind == "paragraph":
            style_bits = []
            if b.align != "left":
                style_bits.append(f"text-align:{b.align}")
            if b.size == "small":
                style_bits.append("font-size:0.8em")
            style = f' style="{";".join(style_bits)}"' if style_bits else ""
            tag = "strong" if b.bold else "span"
            inner = _esc(b.text).replace("\n", "<br>")
            parts.append(f"<p{style}><{tag}>{inner}</{tag}></p>")
        elif b.kind == "table":
            rows_html = "".join(
                "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>" for row in b.rows
            )
            parts.append(f"<table border=\"1\">{rows_html}</table>")
        elif b.kind == "spacer":
            parts.append("<br>")
        elif b.kind == "page_break":
            parts.append("<hr>")
        elif b.kind == "raw_html":
            parts.append(b.raw_html)
    parts.append("</body></html>")
    return "\n".join(parts)


def render_html_bytes(blocks: list[Block], *, title: str, lang: str, encoding: str) -> bytes:
    charset_label = _CHARSET_LABELS[encoding]
    text = render_html_string(blocks, title=title, lang=lang, charset_label=charset_label)
    if encoding == "utf-8-bom":
        return text.encode("utf-8-sig")
    return text.encode(encoding)


def render_html(blocks: list[Block], path: Path, *, title: str, lang: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_html_bytes(blocks, title=title, lang=lang, encoding=encoding))
