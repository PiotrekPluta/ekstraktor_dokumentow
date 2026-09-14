"""The 8 unrecognisable files (DATA_SPEC.md §7), each with a distinct,
nameable defect.
"""

from __future__ import annotations

import io
import random
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas as rl_canvas

from .render_pdf import FONT_NAME, ensure_font_registered

CORRUPT_SPECS = {
    "pusty.txt": "zero bytes",
    "raport_uciety.pdf": "PDF header + truncated mid-object",
    "skan_umowy.pdf": "valid PDF, image-only, no text layer",
    "zabezpieczona.pdf": "password-protected",
    "logo.pdf": "a PNG renamed to .pdf (magic bytes disagree with extension)",
    "umowa.docx": "not a valid zip archive",
    "oferta.docx": "valid zip, missing word/document.xml",
    "dane.txt": "random bytes, no decodable text in any candidate encoding",
}


def make_pusty_txt(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def make_raport_uciety_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, invariant=1)
    c.drawString(100, 700, "Raport kwartalny - strona 1")
    c.showPage()
    c.drawString(100, 700, "Raport kwartalny - strona 2")
    c.showPage()
    c.save()
    full = buf.getvalue()
    # Cut well before %%EOF, mid-object: a reader must fail cleanly, not hang.
    cut_at = int(len(full) * 0.4)
    path.write_bytes(full[:cut_at])


def make_skan_umowy_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(img)
    draw.text((80, 80), "UMOWA (obraz - brak warstwy tekstowej)", fill="black")
    draw.rectangle([60, 60, 1180, 1694], outline="black", width=2)
    png_bytes = io.BytesIO()
    img.save(png_bytes, format="PNG")
    png_bytes.seek(0)

    from reportlab.lib.utils import ImageReader

    c = rl_canvas.Canvas(str(path), pagesize=(1240, 1754), invariant=1)
    c.drawImage(ImageReader(png_bytes), 0, 0, width=1240, height=1754)
    c.showPage()
    c.save()


def make_zabezpieczona_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(path), invariant=1, encrypt="tajne_haslo_2026")
    ensure_font_registered()
    c.setFont(FONT_NAME, 12)
    c.drawString(100, 700, "Dokument zabezpieczony hasłem.")
    c.showPage()
    c.save()


def make_logo_pdf(path: Path) -> None:
    """A PNG whose bytes are written under a .pdf extension — magic bytes
    disagree with the extension."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (256, 128), "white")
    draw = ImageDraw.Draw(img)
    draw.ellipse([20, 20, 108, 108], fill=(30, 90, 160))
    draw.text((120, 55), "LOGO", fill="black")
    img.save(path, format="PNG")


def make_umowa_docx(rng: random.Random, path: Path) -> None:
    """Random bytes under a .docx extension — not a valid zip archive at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = bytes(rng.randrange(256) for _ in range(2048))
    path.write_bytes(payload)


def make_oferta_docx(path: Path) -> None:
    """A syntactically valid zip archive missing the mandatory
    ``word/document.xml`` part."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("[Content_Types].xml", date_time=(2026, 1, 1, 0, 0, 0))
        z.writestr(
            info,
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>",
        )
        info2 = zipfile.ZipInfo("_rels/.rels", date_time=(2026, 1, 1, 0, 0, 0))
        z.writestr(
            info2,
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>",
        )


def make_dane_txt(rng: random.Random, path: Path) -> None:
    """High-entropy random bytes: no BOM, invalid as UTF-8/UTF-16, and not
    linguistically plausible text under any single-byte codec either."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = bytes(rng.randrange(256) for _ in range(512))
    path.write_bytes(payload)


def generate_all(rng: random.Random, corpus_root: Path, rel_dir: str) -> dict[str, str]:
    """Write all 8 corrupt files under ``corpus_root/rel_dir`` and return a
    mapping of filename -> corpus-relative path."""
    out: dict[str, str] = {}
    base = corpus_root / rel_dir
    make_pusty_txt(base / "pusty.txt")
    make_raport_uciety_pdf(base / "raport_uciety.pdf")
    make_skan_umowy_pdf(base / "skan_umowy.pdf")
    make_zabezpieczona_pdf(base / "zabezpieczona.pdf")
    make_logo_pdf(base / "logo.pdf")
    make_umowa_docx(rng, base / "umowa.docx")
    make_oferta_docx(base / "oferta.docx")
    make_dane_txt(rng, base / "dane.txt")
    for name in CORRUPT_SPECS:
        out[name] = f"{rel_dir}/{name}"
    return out
