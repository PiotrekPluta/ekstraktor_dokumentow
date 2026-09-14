"""Plain-text rendering."""

from __future__ import annotations

from pathlib import Path

from .models import Block


def render_txt_string(blocks: list[Block]) -> str:
    lines: list[str] = []
    for b in blocks:
        if b.kind == "heading":
            lines.append(b.text.upper())
            lines.append("=" * len(b.text))
        elif b.kind == "paragraph":
            text = b.text
            if b.align == "right":
                text = text.rjust(max(len(text), 60))
            lines.append(text)
        elif b.kind == "table":
            for row in b.rows:
                lines.append(" | ".join(row))
        elif b.kind == "spacer":
            lines.append("")
        elif b.kind == "page_break":
            lines.append("\f")
        elif b.kind == "raw_html":
            lines.append(b.raw_html)
        lines.append("")
    return "\n".join(lines)


def render_txt_bytes(blocks: list[Block], encoding: str) -> bytes:
    text = render_txt_string(blocks)
    if encoding == "utf-8-bom":
        return text.encode("utf-8-sig")
    return text.encode(encoding)


def render_txt(blocks: list[Block], path: Path, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_txt_bytes(blocks, encoding))
