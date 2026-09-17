"""Stage 8: `eval` (docs/ZADANIE.md §3, docs/DATA_SPEC.md §8).

Joins `expected.jsonl` to the db through `files.path` (both sides are
relative-to-corpus-root, forward-slash, NFC-normalised paths — `files.path`
already comes out of `inventory.py` that way, matching DATA_SPEC.md §8's
`expected.jsonl` format rule exactly, so the join is a plain string lookup,
not a fuzzy one) and scores the seven structured fields by *normalised*
exact match, per document in `expected.jsonl`.

Normalisation here is deliberately **not** `extractor.normalize`/
`extractor.validation` reused: those already ran once, inside the pipeline,
before a value ever reached the db, so the db's own values are already in
canonical form. Reusing the same normalisers here would also mean a bug in
them cancels itself out of `eval`'s score instead of showing up as a
mismatch — the same reasoning docs/DATA_SPEC.md's R0.2 applies to keeping
the generator itself independent of the extractor. `eval`'s normalisers
below are intentionally small, separate, and re-derived from the
`expected.jsonl` format rules (DATA_SPEC.md §8) rather than from the
extractor's code.

Scoring denominator: every document in `expected.jsonl` counts once for
every field, whether or not the db has a matching, finished record for it.
A document `run` never got to (still `pending` at `--limit`/`--budget`) or
one this tool quarantined scores as a plain mismatch on every field where
`expected` isn't `null` — not excluded from the denominator. An accuracy
number that improved by quarantining more documents would be worse than
useless, so quarantining is never rewarded here the way it correctly is
*not* penalised by `report` (docs/ZADANIE.md requirement 7 explicitly says
quarantine is not the tool's failure).

`not_found_in_db` is a separate, narrower diagnostic: a document whose
`files` paths don't resolve to *any* row in `files` at all (as opposed to
resolving to a row that's merely `pending`/`quarantined`) — normally zero
for a full run over the archive `expected.jsonl` describes, since
`inventory.build_inventory` registers a `files`/`documents` row for every
discovered path regardless of what happens to it afterwards. A nonzero
count here means `eval` is being run against a different archive, or a
subset of one, than the one `expected.jsonl` was generated from.

`dedup_mismatches` flags the opposite disagreement: an expected document's
`files` list resolving to *more than one* distinct `documents.id` in the
db, i.e. this tool's dedup failed to merge files the ground truth says are
the same document. Counted and reported, not silently ignored, but the
record is still scored (against one of the disagreeing document ids,
chosen deterministically) rather than dropped, so a dedup bug doesn't also
erase the record from every field's denominator.

`summary` is scored separately, by a documented loose metric, never folded
into the seven-field accuracy above (DATA_SPEC.md §8: "exact match is
meaningless... report it as a separate column so it never inflates the
headline accuracy"). The metric checks, for every expected document with a
non-null `doc_type` (nothing meaningful to check for a quarantined/
unrecognised one — its expected `summary` is `""`):

1. the produced summary is non-empty and shaped like one sentence (no
   internal `.`/`!`/`?` before an optional single trailing one) — a cheap
   proxy for "one sentence", not a real sentence-boundary detector;
2. if `counterparty_name` is known, its longest non-generic word (see
   `_GENERIC_NAME_WORDS`) appears in the summary, case-insensitively;
3. if `doc_type` has a keyword list below, at least one of its words
   appears in the summary.

This is a heuristic, not NLP: no language-match check is attempted at all
(would need a real language-detection dependency for one field of an
already-scored-separately metric) — a stated, deliberate gap, not an
oversight. `_GENERIC_NAME_WORDS` is a short, hand-picked stoplist of
Polish/English legal-form and generic-business words, not a real NER
model; a counterparty name with no non-generic word (e.g. a name that is
*only* a legal form) falls back to its single longest word regardless.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

_STRUCTURED_FIELDS = (
    "doc_type",
    "counterparty_name",
    "counterparty_tax_id",
    "issue_date",
    "due_date",
    "gross_amount",
    "currency",
)

_DOC_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "invoice": ("faktur", "invoice"),
    "contract": ("umow", "umów", "contract"),
    "offer": ("ofert", "offer"),
    # "correspondence"/"other" have no reliable single keyword; not checked.
}

_GENERIC_NAME_WORDS = {
    "sp", "z", "o", "oo", "spolka", "spółka", "akcyjna", "sa",
    "zaklad", "zakład", "uslugowy", "usługowy", "uslugi", "usługi",
    "przedsiebiorstwo", "przedsiębiorstwo", "handlowe", "handlowy",
    "firma", "biuro", "serwis", "grupa", "zarzad", "zarząd",
    "nieruchomosci", "nieruchomości", "company", "corp", "corporation",
    "inc", "ltd", "gmbh", "limited", "group", "service", "services",
    "the", "and", "of",
}  # fmt: skip


@dataclass
class FieldScore:
    field: str
    correct: int
    total: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0


@dataclass
class EvalResult:
    total_documents: int
    not_found: int
    dedup_mismatches: int
    field_scores: list[FieldScore]
    summary_scored: int
    summary_loose_score: float


def load_expected(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def evaluate(conn: sqlite3.Connection, expected: list[dict]) -> EvalResult:
    scores = {f: FieldScore(field=f, correct=0, total=0) for f in _STRUCTURED_FIELDS}
    not_found = 0
    dedup_mismatches = 0
    summary_hits = 0
    summary_total = 0

    for record in expected:
        doc_id, mismatch = _resolve_document_id(conn, record["files"])
        if mismatch:
            dedup_mismatches += 1

        row = None
        if doc_id is not None:
            row = conn.execute(
                "SELECT doc_type, counterparty_name, counterparty_tax_id, "
                "issue_date, due_date, gross_amount, currency, summary "
                "FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
        if row is None:
            not_found += 1

        for field in _STRUCTURED_FIELDS:
            scores[field].total += 1
            actual_value = row[field] if row is not None else None
            if _field_matches(field, record.get(field), actual_value):
                scores[field].correct += 1

        if record.get("doc_type") is not None:
            summary_total += 1
            actual_summary = row["summary"] if row is not None else ""
            if _summary_loose_ok(actual_summary, record):
                summary_hits += 1

    return EvalResult(
        total_documents=len(expected),
        not_found=not_found,
        dedup_mismatches=dedup_mismatches,
        field_scores=list(scores.values()),
        summary_scored=summary_total,
        summary_loose_score=(summary_hits / summary_total) if summary_total else 1.0,
    )


def _resolve_document_id(
    conn: sqlite3.Connection, files: list[str]
) -> tuple[str | None, bool]:
    ids: set[str] = set()
    for path in files:
        normalized = unicodedata.normalize("NFC", path)
        row = conn.execute(
            "SELECT document_id FROM files WHERE path = ?", (normalized,)
        ).fetchone()
        if row is not None:
            ids.add(row[0])
    if not ids:
        return None, False
    if len(ids) > 1:
        return min(ids), True
    return next(iter(ids)), False


def _field_matches(field: str, expected_value: object, actual_value: object) -> bool:
    normalize = _NORMALIZERS[field]
    return normalize(expected_value) == normalize(actual_value)


def _norm_plain(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _norm_name(value: object) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFC", str(value)).strip().casefold()
    text = re.sub(r"\s+", " ", text)
    return text or None


def _norm_tax_id(value: object) -> str | None:
    if value is None:
        return None
    text = re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()
    return text or None


def _norm_currency(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _norm_amount(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


_NORMALIZERS = {
    "doc_type": _norm_plain,
    "counterparty_name": _norm_name,
    "counterparty_tax_id": _norm_tax_id,
    "issue_date": _norm_plain,
    "due_date": _norm_plain,
    "gross_amount": _norm_amount,
    "currency": _norm_currency,
}

_SENTENCE_END_RE = re.compile(r"[.!?]")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _summary_loose_ok(summary: str, expected: dict) -> bool:
    if expected.get("doc_type") is None:
        return True

    text = (summary or "").strip()
    if not text:
        return False

    core = text.rstrip(".!?")
    if _SENTENCE_END_RE.search(core):
        return False

    lowered = text.casefold()

    name = expected.get("counterparty_name")
    if name:
        token = _significant_name_token(str(name))
        if token and token not in lowered:
            return False

    keywords = _DOC_TYPE_KEYWORDS.get(expected["doc_type"])
    return not keywords or any(kw in lowered for kw in keywords)


def _significant_name_token(name: str) -> str | None:
    tokens = _WORD_RE.findall(name)
    if not tokens:
        return None
    significant = [t for t in tokens if t.casefold() not in _GENERIC_NAME_WORDS]
    pool = significant or tokens
    return max(pool, key=len).casefold()


def format_eval_text(result: EvalResult) -> str:
    lines = [
        f"documents_expected = {result.total_documents}",
        f"not_found_in_db    = {result.not_found}",
        f"dedup_mismatches   = {result.dedup_mismatches}",
        "",
        f"{'field':<22}{'correct/total':>16}{'accuracy':>12}",
    ]
    for score in result.field_scores:
        ratio = f"{score.correct}/{score.total}"
        lines.append(f"{score.field:<22}{ratio:>16}{score.accuracy:>12.1%}")
    summary_line = (
        f"summary (loose metric, scored on {result.summary_scored} document(s), "
        "not part of the accuracy above — see docs/DATA_SPEC.md §8):"
    )
    lines += ["", summary_line, f"  {result.summary_loose_score:.1%}"]
    return "\n".join(lines)
