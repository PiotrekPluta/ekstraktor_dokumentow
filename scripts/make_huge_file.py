#!/usr/bin/env python3
"""Generate the oversized streaming-test files (DATA_SPEC.md §6).

Both outputs are git-ignored (``data/corpus/huge_*``) and recreated by this
script rather than committed. Requires ``data/expected.jsonl`` to already
exist (run ``generate_data.py`` first) — this script appends its own two
ground-truth rows to it.

Usage: uv run python scripts/make_huge_file.py [--out data] [--fast]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import unicodedata
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from generator import amounts, content, identities, manifest, nip
from generator import dates as gdates
from generator.models import DocType, Document
from generator.render_txt import render_txt_string

FULL_TARGET_BYTES = 300 * 1024 * 1024  # >= 300 MB, per spec
SMALL_TARGET_BYTES = 30 * 1024 * 1024  # ~30 MB fast-path variant
FAST_TARGET_BYTES = 2 * 1024 * 1024  # for local dev iteration only


def _log_line(i: int) -> str:
    hour = i % 24
    minute = (i * 7) % 60
    second = (i * 13) % 60
    node = 1 + (i % 96)
    return (
        f"2024-01-{1 + (i % 28):02d} {hour:02d}:{minute:02d}:{second:02d} [INFO] "
        f"Automatyczny odczyt licznika ciepła - węzeł nr {node:03d} - wartość OK - "
        f"cykl {i}\n"
    )


_SECURGUARD_TAX_ID = nip.generate_valid_nip(random.Random("huge-securguard"))


def _real_contract_document(doc_id: str) -> Document:
    seller = identities.party(
        "SecurGuard Ochrona Obiektów Sp. z o.o.",
        tax_id=_SECURGUARD_TAX_ID,
        addr=identities.address("ul. Strażacka 6", "60-101", "Poznań", "PL", "Polska"),
    )
    issue = date(2024, 2, 14)
    term_end = date(2025, 2, 13)
    value = Decimal("54000.00")
    buyer = identities.BUYER_MAIN
    blocks = [
        content.heading("Umowa o świadczenie usług ochrony nr U/2024/02/007"),
        *content.party_lines(seller, "Wykonawca:", tax_id_display=nip.render_plain(seller.tax_id)),
        content.spacer(),
        *content.party_lines(buyer, "Zamawiający:"),
        content.spacer(),
        *content.numbered_clauses([
            f"Umowa zawarta dnia {gdates.polish_long(issue)} na czas określony do "
            f"dnia {gdates.polish_long(term_end)}.",
            "Wykonawca świadczy usługi całodobowej ochrony fizycznej nieruchomości "
            "wspólnej zgodnie z załączonym harmonogramem.",
            f"Łączne wynagrodzenie Wykonawcy wynosi {amounts.format_pl_space_zl(value)} "
            "za cały okres obowiązywania umowy.",
        ]),
        *content.signature_block(seller, buyer),
    ]
    return Document(
        doc_id=doc_id, doc_type=DocType.CONTRACT, render_format="txt", language="pl",
        counterparty=seller, issue_date=issue, due_date=term_end,
        gross_amount=value, currency="PLN",
        summary="Umowa ochrony obiektu z SecurGuard Ochrona Obiektów.",
        blocks=blocks,
        tags=["huge-file-streaming"],
    )


def _write_streamed(path: Path, *, contract_text: str, target_bytes: int, contract_at_end: bool) -> int:
    """Write ``path`` by streaming filler log lines one at a time (never
    holding the whole file in memory), with the real contract text placed
    either at the very start or the very end of the byte stream."""
    path.parent.mkdir(parents=True, exist_ok=True)
    contract_bytes = contract_text.encode("cp1250")
    filler_target = max(target_bytes - len(contract_bytes), 0)
    written = 0
    with path.open("wb") as f:
        if not contract_at_end:
            f.write(contract_bytes)
            written += len(contract_bytes)
        i = 0
        filler_written = 0
        while filler_written < filler_target:
            encoded = _log_line(i).encode("cp1250", errors="replace")
            f.write(encoded)
            filler_written += len(encoded)
            i += 1
        written += filler_written
        if contract_at_end:
            f.write(contract_bytes)
            written += len(contract_bytes)
    return written


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def append_to_expected(out_dir: Path, docs: list[Document]) -> None:
    expected_path = out_dir / "expected.jsonl"
    existing = []
    if expected_path.exists():
        existing = [json.loads(line) for line in expected_path.open(encoding="utf-8")]
    existing_files = {f for row in existing for f in row["files"]}
    new_rows = []
    for doc in docs:
        row = doc.expected_row()
        row["files"] = sorted(nfc(f) for f in row["files"])
        if row["files"][0] not in existing_files:
            new_rows.append(row)
    all_rows = existing + new_rows
    all_rows.sort(key=lambda r: r["files"][0] if r["files"] else "")
    with expected_path.open("w", encoding="utf-8", newline="\n") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--fast", action="store_true", help="shrink targets for local dev iteration")
    args = parser.parse_args()

    corpus_root = args.out / "corpus"
    full_target = FAST_TARGET_BYTES if args.fast else FULL_TARGET_BYTES
    small_target = FAST_TARGET_BYTES // 4 if args.fast else SMALL_TARGET_BYTES

    doc_end = _real_contract_document("huge01")
    doc_start = _real_contract_document("huge02")
    doc_start.summary = "Umowa ochrony obiektu z SecurGuard Ochrona Obiektów (wariant z polami na początku)."

    text_end = render_txt_string(doc_end.blocks)
    text_start = render_txt_string(doc_start.blocks)

    path_end = corpus_root / "huge_log_appendix.txt"
    path_start = corpus_root / "huge_log_small.txt"

    written_end = _write_streamed(path_end, contract_text=text_end, target_bytes=full_target, contract_at_end=True)
    written_start = _write_streamed(path_start, contract_text=text_start, target_bytes=small_target, contract_at_end=False)

    doc_end.files = ["huge_log_appendix.txt"]
    doc_start.files = ["huge_log_small.txt"]

    append_to_expected(args.out, [doc_end, doc_start])

    manifest_path = args.out / "MANIFEST.md"
    if manifest_path.exists() and "huge_log_appendix.txt" not in manifest_path.read_text(encoding="utf-8"):
        manifest.add_streaming_test_files(
            manifest_path,
            [
                ("huge_log_appendix.txt", "huge-file-streaming, real-doc-at-end"),
                ("huge_log_small.txt", "huge-file-streaming, real-doc-at-start"),
            ],
        )

    print(f"Wrote {path_end} ({written_end / (1024*1024):.1f} MB, contract at end)")
    print(f"Wrote {path_start} ({written_start / (1024*1024):.1f} MB, contract at start)")
    print(f"Appended 2 rows to {args.out / 'expected.jsonl'}")


if __name__ == "__main__":
    main()
