# Architecture

Status: Stages 0–3 built (skeleton, SQLite schema, inventory + dedup,
hardened extraction). This document grows with each stage; it does not yet
cover the model backend, orchestration/resume, or budget enforcement
(Stages 5–7).

## Key decisions

**Document identity is the dedup hash itself, not a surrogate key.**
`documents.id` is the sha256 of normalised extracted text when extraction
succeeds, or of raw bytes when it doesn't (`identity_kind` records which).
"Processing order deterministic, sorted by document id" (requirement 3)
falls out of `ORDER BY id` for free, and two byte-identical corrupt files
collapse into one quarantined document with no extra logic.

**Two-level dedup**: byte sha256 first (exact copies, cheap), then a hash
of normalised text (NFC, whitespace-collapsed, case-folded, decorative
divider lines like `====` stripped). Deliberately narrow — no fuzzy/
near-duplicate matching. Case-folding and divider-stripping were added
only once needed to dedup one letter rendered to `.docx`/`.html`/`.txt`
with different per-format capitalisation; matching near-identical invoices
from one template is explicitly not attempted, since those must stay
distinct documents.

**Schema invariants are CHECK constraints, not just application logic**:
`quarantined ⟺ has a reason` (closed enum), `done ⟹ doc_type is set`.

**`.eml` attachment handling**: a real, extractable attachment's text —
not the covering note — becomes the file's content (an email forwarding an
invoice IS that invoice); otherwise the body is used. The attachment's
declared filename is never used to build a filesystem path, which
neutralises path-traversal-shaped names structurally rather than via
sanitisation. `files.content_source` records which source won, purely for
`sqlite3` inspectability — `files.path` always stays the `.eml`'s own real
path, so `eval`'s join against `expected.jsonl` needs no synthetic path.

**Model/backend** (config-selectable, per requirement 2 — not yet wired):
`llama-server` primary, Ollama secondary, `fake` for tests. Concurrent
requests to the model is a backend server-slot parameter, not `--workers`;
conflating the two would queue requests past their timeout for no gain.

**Extraction runs isolated per file, in a subprocess with a wall-clock
timeout** (`multiprocessing`, forced `spawn` on every platform — matches
macOS's own default, so pickling bugs surface in dev). A stuck C call
inside pypdfium2 can't be interrupted by a Python-level timeout, so this
has to wrap extraction from one layer up. A hang past the timeout kills
the process (`parse_timeout`); a crash is `corrupt_file`. The parent reads
the child's result queue *before* joining it — a payload bigger than the
OS pipe buffer (real PDF text easily is) blocks the child in `put()` until
read, so joining first deadlocks the two against each other.

**Materialisation matches each format's needs.** Directory input already
has a real path, so PDF/DOCX open there directly — their structural data
(zip central directory, PDF xref table) lives at the file's *end*, so any
truncated read risks corrupting exactly those bytes. Zip input has no
standalone path; PDF/DOCX entries from a zip spool to a temp file first
(same guarantee), while TXT/HTML — genuinely prefix-truncatable — stay a
bounded 8MB in-memory buffer, no disk I/O.

## Known limitations

- No OCR: a scanned/image-only PDF is quarantined (`no_text_layer`).
- Nested `.eml`-in-`.eml` attachments are not followed.
- `.eml` is read in full (never realistically huge), except when
  zip-sourced, where it gets the 8MB cap like TXT/HTML — a minor
  inconsistency, not a correctness bug.
- Zip input trusts nothing it reads: entries are only ever read through
  the zip handle, never extracted to disk, so zip-slip has no attack
  surface to begin with; an oversized declared entry is rejected before
  any decompression (zip-bomb guard).

## Throughput bottleneck (requirement 5)

Design intent, to confirm once Stage 5 lands: one inference server, one
GPU. Prompt processing is compute-bound, so parallel server slots barely
help beyond a small number — near-sequential throughput should be assumed.
The dominant lever on latency is context size sent to the model, not
model size, which is why Stage 4 windows long documents instead of
sending full text. `--workers` controls concurrency for everything
*except* the model call; it doesn't increase model concurrency, a
separate backend-side slot count.

## At 100× scale

Single-writer SQLite (WAL serialises writes) and one inference server both
stop being enough well before 100× the ~40-file evaluation archive. What
would change: a real task queue instead of workers polling one file,
Postgres instead of SQLite, and request batching across documents instead
of one document per call. Byte-level dedup already scales linearly
(streamed sha256, no pairwise comparison) — nothing there needs to change.

Spawning a subprocess per file for extraction costs ~300ms (interpreter
startup, reimporting pypdfium2/python-docx) — negligible at 40 files, but
4000 files means ~20 minutes of pure spawn overhead before any parsing.
At that scale extraction would need a persistent worker pool instead of
spawn-per-file, trading some isolation granularity for throughput.
