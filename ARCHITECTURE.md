# Architecture

Status: Stages 0–2 built (skeleton, SQLite schema, inventory + dedup). This
document grows with each stage; it does not yet cover the model backend,
orchestration/resume, or budget enforcement (Stages 5–7).

## Key decisions

**Document identity is the dedup hash itself, not a surrogate key.**
`documents.id` is the sha256 of normalised extracted text when extraction
succeeds, or of raw bytes when it doesn't (`identity_kind` records which).
Two consequences: "processing order deterministic, sorted by document id"
(requirement 3) falls out of `ORDER BY id` for free, and two byte-identical
corrupt files collapse into one quarantined document with no extra logic.

**Two-level dedup**: byte sha256 first (catches exact copies cheaply),
then a hash of normalised text (NFC, whitespace-collapsed, case-folded,
decorative divider lines like `====` stripped). The second level is
deliberately narrow — no fuzzy/near-duplicate matching. Case-folding and
divider-stripping were added only after they were needed to correctly
dedup one letter rendered to `.docx`/`.html`/`.txt` with genuinely
different capitalisation conventions per format; going further (e.g.
matching near-identical invoices from one template) is explicitly not
attempted, because those are meant to stay distinct documents.

**Schema invariants are CHECK constraints, not just application logic**:
`quarantined ⟺ has a reason` (closed enum), `done ⟹ doc_type is set`. A
row can only lack a `doc_type` if it's quarantined.

**`.eml` attachment handling**: if an attachment is present and itself
extracts to non-empty text, that text — not the covering note — becomes
the file's content (an email forwarding an invoice IS that invoice).
Otherwise the body is used. The attachment's declared filename is never
used to build a filesystem path (only its bytes are read), which
neutralises path-traversal-shaped attachment names structurally rather
than via a sanitisation step. `files.content_source` records which source
won, purely for `sqlite3` inspectability — `files.path` always stays the
`.eml`'s own real path, so `eval`'s join against `expected.jsonl` never
needs a synthetic path.

**Model/backend** (config-selectable, per requirement 2 — not yet wired):
`llama-server` primary, Ollama secondary, `fake` for tests. Concurrent
requests to the model is a backend server-slot parameter, not `--workers`;
conflating the two would queue requests past their timeout and burn budget
for no throughput gain.

## Known limitations

- No subprocess isolation or per-file timeout yet — a pathological file
  can hang `run` today. Stage 3 closes this.
- Text extraction buffers at most 8MB of any entry. Exact and safe for
  TXT/HTML (flat, prefix-truncatable formats); blunt for PDF/DOCX/EML,
  whose structure (zip central directory, PDF xref table) lives at the
  end of the file — a legitimately huge file of those formats would
  currently misclassify as `corrupt_file`. Every real document in this
  corpus is well under the cap; the correct fix (page-by-page PDF reading
  with a character cap) is Stage 3's job, not this one's.
- No OCR: a scanned/image-only PDF is quarantined (`no_text_layer`), not
  processed.
- Nested `.eml`-in-`.eml` attachments are not followed.
- Zip input trusts nothing it reads: entries are only ever read through
  the zip handle, never extracted to disk, so zip-slip has no attack
  surface to begin with; a declared-oversized entry is rejected before
  any decompression (zip-bomb guard).

## Throughput bottleneck (requirement 5)

Design intent, to be confirmed once Stage 5 lands: one inference server,
one GPU. Prompt processing is compute-bound, so parallel server slots
barely help beyond a small number — near-sequential throughput should be
assumed. The dominant lever on latency is context size sent to the model,
not model size, which is why Stage 4 windows long documents (head/tail/
keyword hits) instead of sending full text. `--workers` controls how many
documents are in flight for everything *except* the model call (I/O,
parsing, regex candidate extraction); it does not increase model
concurrency, which is a separate, backend-side slot count.

## At 100× scale

Single-writer SQLite (WAL mode serialises writes) and one inference
server both stop being enough well before 100× the ~40-file evaluation
archive. What would change: a real task queue instead of workers polling
one file, Postgres instead of SQLite for concurrent writers, and an
inference server with real request batching across documents instead of
one document per call. Byte-level dedup already scales linearly (streamed
sha256, no pairwise comparison); nothing there needs to change.
