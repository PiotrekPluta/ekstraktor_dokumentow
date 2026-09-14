"""Acceptance checks for the synthetic corpus generator (DATA_SPEC.md §10).

Imports only ``scripts.generator.*`` / the generator script itself — never
anything from ``extractor`` (R0.2).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import generate_data


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("corpus_gen")
    sys.argv = ["generate_data.py", "--out", str(out_dir)]
    generate_data.main()
    rows = [json.loads(line) for line in (out_dir / "expected.jsonl").open(encoding="utf-8")]
    manifest_text = (out_dir / "MANIFEST.md").read_text(encoding="utf-8")
    meta = json.loads((out_dir / "_generator_meta.json").read_text(encoding="utf-8"))
    return {
        "out_dir": out_dir,
        "corpus": out_dir / "corpus",
        "rows": rows,
        "manifest": manifest_text,
        "meta": meta,
    }


def all_corpus_files(corpus: Path) -> set[str]:
    return {nfc(str(p.relative_to(corpus))) for p in corpus.rglob("*") if p.is_file()}


# --- §10.1 reproducibility ---------------------------------------------------

def test_reproducible_byte_identical(tmp_path_factory):
    dir1 = tmp_path_factory.mktemp("gen1")
    dir2 = tmp_path_factory.mktemp("gen2")
    sys.argv = ["generate_data.py", "--out", str(dir1)]
    generate_data.main()
    sys.argv = ["generate_data.py", "--out", str(dir2)]
    generate_data.main()

    files1 = sorted(p.relative_to(dir1) for p in dir1.rglob("*") if p.is_file())
    files2 = sorted(p.relative_to(dir2) for p in dir2.rglob("*") if p.is_file())
    assert files1 == files2

    for rel in files1:
        assert sha256_of(dir1 / rel) == sha256_of(dir2 / rel), f"{rel} differs between runs"


# --- §10.2 files <-> expected.jsonl correspondence --------------------------

def test_every_expected_file_exists_and_is_referenced_once(generated):
    corpus = generated["corpus"]
    rows = generated["rows"]

    listed = [f for r in rows for f in r["files"]]
    assert len(listed) == len(set(listed)), "a file is referenced by more than one document"

    disk_files = all_corpus_files(corpus)
    assert set(listed) == disk_files, (
        f"mismatch: in expected.jsonl but not on disk: {set(listed) - disk_files}; "
        f"on disk but not referenced: {disk_files - set(listed)}"
    )


# --- §10.3 manifest balance --------------------------------------------------

def test_manifest_balance_numbers(generated):
    m = generated["manifest"]
    match = re.search(
        r"input_files\s*=\s*(\d+)\s*duplicate_files\s*=\s*(\d+)\s*unique_documents\s*=\s*(\d+)",
        m.replace("\n", " "),
    )
    assert match, "balance block not found in MANIFEST.md"
    input_files, duplicate_files, unique_documents = (int(x) for x in match.groups())
    assert input_files == duplicate_files + unique_documents

    corpus_file_count = len(all_corpus_files(generated["corpus"]))
    assert corpus_file_count == input_files

    rows = generated["rows"]
    assert len(rows) == unique_documents
    processed_ok = sum(1 for r in rows if r["doc_type"] is not None)
    quarantined = sum(1 for r in rows if r["doc_type"] is None)
    assert processed_ok + quarantined == unique_documents


# --- §10.4 every property tag appears in the manifest -----------------------

REQUIRED_TAGS = [
    "nip-checksum-fail", "buyer-nip-first", "buyer-nip-prominent",
    "decoy-regon", "decoy-krs", "decoy-iban", "foreign-vat-id",
    "amount-nbsp-separator", "amount-narrow-nbsp", "amount-in-words",
    "correction-invoice", "negative-amount", "amount-multipage-subtotal",
    "currency-symbol-unambiguous-zl", "currency-symbol-ambiguous",
    "currency-only-PLN-code", "currency-from-seller-country",
    "currency-from-vat-prefix", "currency-from-vat-prefix-non-euro",
    "currency-address-beats-prefix", "currency-buyer-prefix-ignored",
    "currency-buyer-address-ignored", "currency-explicit-overrides-address",
    "currency-unresolvable", "due-date-derived", "date-format-polish-long",
    "date-format-roman", "date-format-us-slash-ambiguous",
    "date-format-english-long", "date-format-iso", "date-format-dotted",
    "name-with-quotes", "name-with-comma", "name-with-diacritics",
    "name-in-letterhead-only", "name-in-signature-only", "name-in-eml-from-only",
    "buyer-decoy-prominent", "buyer-precedes-seller", "no-tax-id",
    "nip-page-280", "long-document", "quotes-english-clause",
    "near-dup-different-doc", "injection-ignore-instructions", "injection-sql",
    "injection-fake-json", "injection-path-write",
    "injection-html-script-iframe-comment", "attachment-absolute-path",
    "attachment-path-traversal", "from-is-buyer", "encoding-cp1250",
    "encoding-iso8859-2", "encoding-utf16le", "encoding-utf8-bom",
]


def test_required_property_tags_present(generated):
    m = generated["manifest"]
    missing = [tag for tag in REQUIRED_TAGS if tag not in m]
    assert not missing, f"tags missing from MANIFEST.md: {missing}"


# --- §10.5 invoice arithmetic -------------------------------------------------

def test_correction_invoice_is_negative(generated):
    rows = generated["rows"]
    correction = [r for r in rows if "korygująca" in " ".join(r["files"])]
    assert correction
    assert correction[0]["gross_amount"].startswith("-")


# --- §10.6 encoding-variant duplicate decodes to identical NFC text ----------

def test_encoding_duplicate_pair_matches(generated):
    corpus = generated["corpus"]
    a = corpus / "Faktury/2024/Rozliczenia Q2/Faktura_FV_2024_07_301.html"
    b = corpus / "Faktury/Kopie robocze/Faktura_FV_2024_07_301_cp1250.html"

    def strip(html_bytes: bytes, encoding: str) -> str:
        text = html_bytes.decode(encoding)
        return nfc(re.sub(r"<[^>]+>", "", text))

    assert strip(a.read_bytes(), "utf-8") == strip(b.read_bytes(), "cp1250")


# --- §10.7 byte-identical duplicate pairs -------------------------------------

def test_identical_copy_pairs_share_sha256(generated):
    corpus = generated["corpus"]
    a = corpus / "Faktury/2024/Dostawcy zewnętrzni/Faktura_FV_2024_03_018.pdf"
    b = corpus / "Faktury/Kopie robocze/Faktura_FV_2024_03_018_kopia.pdf"
    assert sha256_of(a) == sha256_of(b)


# --- §10.8 near-duplicate pairs differ in sha256 and normalised text ---------

def test_near_duplicate_pairs_differ(generated):
    corpus = generated["corpus"]
    pairs = [
        (
            "Faktury/2024/Rozliczenia Q2/Faktura_FV_2024_07_301.html",
            "Faktury/2024/Rozliczenia Q2/Faktura_FV_2024_07_302.html",
        ),
        (
            "Umowy/Załączniki/2024/Umowa_U_2024_04_009_wariant_A.docx",
            "Umowy/Załączniki/2024/Umowa_U_2024_04_009_wariant_B.docx",
        ),
    ]
    for rel_a, rel_b in pairs:
        a, b = corpus / rel_a, corpus / rel_b
        assert sha256_of(a) != sha256_of(b)


# --- §10.9 no buyer identity leaks into expected.jsonl as a counterparty ----

def test_no_buyer_identity_leaks(generated):
    buyer_names = {b["name"] for b in generated["meta"]["buyer_identities"]}
    buyer_tax_ids = {b["tax_id"] for b in generated["meta"]["buyer_identities"]}
    for row in generated["rows"]:
        assert row["counterparty_name"] not in buyer_names
        if row["counterparty_tax_id"]:
            assert row["counterparty_tax_id"] not in buyer_tax_ids


# --- §10.10 inferred-currency documents match the precedence table ----------

def test_currency_precedence_traps(generated):
    from generator import currency

    assert currency.resolve_currency(seller_has_address=True, seller_country_code="PL") == ("PLN", "address")
    assert currency.resolve_currency(seller_has_address=True, seller_country_code="DE") == ("EUR", "address")
    assert currency.resolve_currency(explicit_code="EUR", seller_has_address=True, seller_country_code="PL") == ("EUR", "explicit")
    assert currency.resolve_currency(seller_has_address=False, seller_vat_prefix="DE") == ("EUR", "vat_prefix")
    assert currency.resolve_currency(seller_has_address=False, seller_vat_prefix="SE") == ("SEK", "vat_prefix")
    assert currency.resolve_currency(seller_has_address=True, seller_country_code="PL", seller_vat_prefix="DE") == ("PLN", "address")
    assert currency.resolve_currency() == (None, "none")


# --- NIP checksum sanity -----------------------------------------------------

def test_nip_checksum_helpers():
    import random

    from generator import nip

    rng = random.Random(1)
    for _ in range(20):
        valid = nip.generate_valid_nip(rng)
        assert nip.is_valid_nip(valid)
        invalid = nip.generate_invalid_nip(rng)
        assert not nip.is_valid_nip(invalid)
