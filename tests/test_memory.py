"""Stage 9 acceptance test for requirement 9 ("szczytowe zużycie pamięci
[...] nie przekracza 2 GB, niezależnie od rozmiaru pojedynczego pliku
wejściowego") on the huge-file case docs/DATA_SPEC.md's corpus spec
requires ("co najmniej jeden plik ma kilkaset megabajtów").

This does **not** assert an absolute 2GB ceiling — that number is a
property of the real M1 evaluation machine and is checked there, by hand,
with `/usr/bin/time -l` (`ARCHITECTURE.md`'s "Stage 10" section), not by a
CI-portable pytest assertion (`ru_maxrss` alone differs in *units* between
Linux and macOS, `ARCHITECTURE.md`'s macOS-pitfalls table). What this test
asserts instead is the structural property that *makes* the 2GB bound
plausible in the first place: processing a several-hundred-megabyte input
file must not make this process's (or its extraction subprocess's)
resident memory grow anywhere close to the file's size — i.e. the file is
genuinely streamed (`inventory.py`'s `HASH_CHUNK_SIZE`-bounded hashing) and
capped (`textextract.py`'s `MAX_TEXT_CHARS`-bounded read), not loaded whole
into memory at any point. A regression to `Path.read_bytes()` on the whole
file would blow well past the margin asserted here.

The huge file is generated fresh into `tmp_path` rather than depending on
`data/corpus`'s real (git-ignored, `scripts/make_huge_file.py`-generated)
fixture: that script also appends ground-truth rows to the committed
`data/expected.jsonl` as a side effect, which a test must never trigger
implicitly, and this way the test is self-contained and always runnable
with no local pre-step.
"""

from __future__ import annotations

import resource
from pathlib import Path

from extractor.db import connect
from extractor.inventory import build_inventory
from extractor.textextract import MAX_TEXT_CHARS

_HUGE_FILE_BYTES = 300 * 1024 * 1024  # matches DATA_SPEC.md's "kilkaset MB"
_LINE = b"Polisa numer 000000: brak istotnej tresci, wypelniacz do testu.\n"

# Generous on purpose (a canary, not a tight bound): correct streaming
# code should use orders of magnitude less than this; only a regression to
# loading the whole file would come close to it.
_MAX_RSS_GROWTH_KB = 150 * 1024


def _write_huge_file(path: Path) -> None:
    with path.open("wb") as fh:
        written = 0
        while written < _HUGE_FILE_BYTES:
            fh.write(_LINE)
            written += len(_LINE)


def _ru_maxrss_kb() -> tuple[int, int]:
    usage_self = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    usage_children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return usage_self, usage_children


def test_processing_a_300mb_file_does_not_grow_memory_close_to_its_size(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    _write_huge_file(input_dir / "huge.txt")

    before_self, before_children = _ru_maxrss_kb()

    db_path = tmp_path / "db.sqlite"
    conn = connect(db_path)
    build_inventory(conn, input_dir)

    after_self, after_children = _ru_maxrss_kb()

    (source_text,) = conn.execute(
        "SELECT source_text FROM documents LIMIT 1"
    ).fetchone()
    conn.close()

    assert source_text is not None
    assert len(source_text) <= MAX_TEXT_CHARS

    self_growth = after_self - before_self
    children_growth = after_children - before_children
    assert self_growth < _MAX_RSS_GROWTH_KB, (
        f"main process RSS grew {self_growth} KB processing a "
        f"{_HUGE_FILE_BYTES // (1024 * 1024)} MB file — looks like it was "
        "read into memory wholesale rather than streamed"
    )
    assert children_growth < _MAX_RSS_GROWTH_KB, (
        f"extraction subprocess RSS grew {children_growth} KB — looks like "
        "the huge file was read into memory wholesale rather than capped"
    )
