#!/usr/bin/env python3
"""Generate the duplicate-heavy stress archive (DATA_SPEC.md §6).

Not a submission deliverable: this is a local dev tool for profiling the
extractor's memory usage and dedup cost on an archive of several thousand
files, ~97% of them duplicates of the ~36 real+corrupt documents already in
``data/corpus/`` (built by ``generate_data.py``). It does not touch
``expected.jsonl`` — accuracy scoring is not its purpose, throughput and
memory under heavy duplication is.

Usage: uv run python scripts/make_scale_archive.py [--out data] [--files 5000]
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

SEED = 20260101
DIR_WORDS = [
    "Archiwum", "Segregator", "Rok 2024", "Oddział", "Region Południe",
    "Region Północ", "Dokumenty", "Skany", "Do przeglądu", "Zewnętrzne",
    "Wewnętrzne", "Kopie", "Backup", "Import", "Migracja",
]


def collect_source_files(corpus: Path) -> list[Path]:
    return sorted(
        p for p in corpus.rglob("*")
        if p.is_file() and not p.name.startswith("huge_")
    )


def random_dir(rng: random.Random, depth: int) -> Path:
    parts = [rng.choice(DIR_WORDS) for _ in range(depth)]
    return Path(*parts)


def make_variant_name(rng: random.Random, original: Path, index: int) -> str:
    stem, suffix = original.stem, original.suffix
    style = rng.randrange(4)
    if style == 0:
        name = f"{stem}_kopia_{index}{suffix}"
    elif style == 1:
        name = f"{stem} ({index}){suffix}"
    elif style == 2:
        name = f"{stem}_{index:05d}{suffix}"
    else:
        name = unicodedata.normalize("NFD", f"{stem}_v{index}{suffix}")
    return name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--files", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    corpus = args.out / "corpus"
    sources = collect_source_files(corpus)
    if not sources:
        raise SystemExit(f"No source files found under {corpus}; run generate_data.py first.")

    scale_root = args.out / "corpus_scale"
    if scale_root.exists():
        shutil.rmtree(scale_root)
    scale_root.mkdir(parents=True)

    rng = random.Random(args.seed)

    # ~3% genuinely unique documents (the originals, copied once each),
    # ~97% duplicates of them scattered through a deep, randomised tree.
    written = 0
    for src in sources:
        dest_dir = scale_root / random_dir(rng, rng.randint(3, 5))
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest_dir / src.name)
        written += 1

    remaining = max(args.files - written, 0)
    for i in range(remaining):
        src = rng.choice(sources)
        dest_dir = scale_root / random_dir(rng, rng.randint(3, 6))
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = make_variant_name(rng, src, i)
        shutil.copy(src, dest_dir / name)
        written += 1

    print(
        f"Wrote {written} files under {scale_root}/ from {len(sources)} source "
        f"documents ({written - len(sources)} duplicates, "
        f"{(written - len(sources)) / written:.0%} of the archive)."
    )
    print(
        "This archive has no expected.jsonl of its own — it exists to profile "
        "memory and dedup throughput, not extraction accuracy."
    )


if __name__ == "__main__":
    main()
