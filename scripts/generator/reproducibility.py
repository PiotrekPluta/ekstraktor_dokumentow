"""Helpers enforcing byte-for-byte reproducible output (DATA_SPEC.md R0.3)."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

FIXED_ZIP_DATE_TIME = (2026, 1, 1, 0, 0, 0)
FIXED_CREATED_ISO = "2026-01-01T00:00:00Z"

_CORE_XML_CREATED_RE = re.compile(rb"(<dcterms:created[^>]*>)[^<]*(</dcterms:created>)")
_CORE_XML_MODIFIED_RE = re.compile(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)")


def normalize_docx(path: Path) -> None:
    """Rewrite a just-saved .docx in place with fixed member timestamps and
    fixed ``docProps/core.xml`` created/modified fields, so two runs on a
    clean tree produce byte-identical files regardless of wall-clock time.
    """
    data = path.read_bytes()
    src = zipfile.ZipFile(io.BytesIO(data))
    infos = src.infolist()
    contents = {info.filename: src.read(info.filename) for info in infos}
    src.close()

    core_name = "docProps/core.xml"
    if core_name in contents:
        core = contents[core_name]
        core = _CORE_XML_CREATED_RE.sub(rb"\g<1>" + FIXED_CREATED_ISO.encode() + rb"\g<2>", core)
        core = _CORE_XML_MODIFIED_RE.sub(rb"\g<1>" + FIXED_CREATED_ISO.encode() + rb"\g<2>", core)
        contents[core_name] = core

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as out:
        for info in infos:
            new_info = zipfile.ZipInfo(info.filename, date_time=FIXED_ZIP_DATE_TIME)
            new_info.compress_type = info.compress_type
            new_info.external_attr = info.external_attr
            out.writestr(new_info, contents[info.filename])
    tmp_path.replace(path)
