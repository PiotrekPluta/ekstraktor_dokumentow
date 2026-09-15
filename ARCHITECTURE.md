# Architecture

Status: Stages 0–4 built (skeleton, SQLite schema, inventory + dedup,
hardened extraction, context windowing), plus Stage 5a (LLM client
interface + `fake` backend). Real backend wiring, model pinning (5b),
orchestration/resume, and budget enforcement (Stages 6–7) aren't built yet.

## Key decisions

**Document identity is the dedup hash itself, not a surrogate key.**
`documents.id` is the sha256 of normalised extracted text when extraction
succeeds, or of raw bytes when it doesn't (`identity_kind` records which).
"Processing order deterministic, sorted by document id" (requirement 3)
falls out of `ORDER BY id` for free, and two byte-identical corrupt files
collapse into one quarantined document with no extra logic.

**Two-level dedup**: byte sha256 first (exact copies, cheap), then a hash
of normalised text (NFC, whitespace-collapsed, case-folded, decorative
divider lines like `====` stripped) — added only once needed to dedup one
letter rendered to `.docx`/`.html`/`.txt` with different per-format
capitalisation. Deliberately narrow: matching near-identical invoices from
one template is explicitly not attempted, since those must stay distinct.

**Schema invariants are CHECK constraints, not just application logic**:
`quarantined ⟺ has a reason` (closed enum), `done ⟹ doc_type is set`.

**`.eml` attachment handling**: a real, extractable attachment's text —
not the covering note — becomes the file's content (an email forwarding an
invoice IS that invoice). Its declared filename is never used to build a
filesystem path, neutralising path-traversal-shaped names structurally
rather than via sanitisation. `files.content_source` records which source
won, purely for `sqlite3` inspectability; `files.path` stays the `.eml`'s
own real path either way, so `eval`'s join needs no synthetic path.

**Model/backend** (config-selectable, per requirement 2): `llama-server`
primary, Ollama secondary — both deferred to Stage 5b, since no model is
pinned yet (below). `fake` is built (Stage 5a) and is the default.
Concurrent requests to the model is a backend server-slot parameter, not
`--workers`; conflating the two would queue requests past their timeout
for no gain.

**Retry/backoff/circuit-breaking is one shared wrapper around every
backend, not per-backend logic.** `ResilientLLMClient` decorates any
`LLMClient` (`fake` today; `llama_server`/`ollama` will be no different
once built) with exponential backoff up to a retry cap, then a circuit
breaker that opens after N *consecutive* failures. Once open it stays
open for the rest of the process — no half-open retry attempt mid-run;
recovery is a resume starting a fresh client with a fresh breaker, not a
cooldown timer within one run. This directly implements requirement 5's
"transient unavailability must not lose a document or hang forever": a
tripped breaker surfaces as `LLMBackendUnavailable`, which Stage 7 will
map to `stop_reason=backend_unavailable` with the in-flight document
still `pending`, never lost.

**The `fake` backend is a first-class, config-selectable backend, not
just a test double.** It returns a canned response and counts every call
(`.calls`), which is exactly what Stage 9's resumability test needs ("no
repeated model calls for completed documents"). It can also be configured
to fail on demand (`fail_first_n`, `always_fail`, a custom
`error_factory`), so the retry/circuit-breaker paths — and, once Stage 7
exists, the whole `run`/resume/`stop_reason=backend_unavailable` path —
are testable end to end without ever touching a real model.

**`LLMRequest`/`LLMResponse` are deliberately generic**: a prompt string,
a JSON schema dict, a token cap in; response text plus token counts out.
The client layer has no notion of the eight extraction fields — building
the prompt from windowed context (Stage 4's output) and parsing/validating
the response into those fields belongs to Stage 6/7, not here. This keeps
the backend abstraction reusable regardless of what's being asked of it.

**Extraction runs isolated per file, in a subprocess with a wall-clock
timeout** (`multiprocessing`, forced `spawn` everywhere — matches macOS's
own default). A stuck C call inside pypdfium2 can't be interrupted by a
Python-level timeout, so this has to wrap extraction from one layer up: a
hang past the timeout kills the process (`parse_timeout`); a crash is
`corrupt_file`. The parent reads the child's result queue *before* joining
it — a payload bigger than the OS pipe buffer (real PDF text easily is)
blocks the child in `put()` until read, so joining first would deadlock
the two.

**Materialisation matches each format's needs.** Directory input has a
real path, so PDF/DOCX open it directly — their structural data (zip
central directory, PDF xref table) lives at the file's *end*, so a
truncated read risks corrupting exactly those bytes. Zip-sourced PDF/DOCX
spool to a temp file first for the same guarantee; TXT/HTML — genuinely
prefix-truncatable — stay a bounded 8MB in-memory buffer.

**Context windowing uses a character budget, not tokens** — no model is
pinned yet (Stage 5b's job). Long documents get head (35%) + tail (25%) +
budget-bounded windows around keyword/regex-candidate hits in the middle,
so a field mentioned once, deep in a 286-page contract, still reaches the
model. Candidate regexes (NIP/date/amount shapes) are deliberately
imprecise — matching decoys too is fine, since windowing only needs anchor
positions; Stage 6 will reuse the same module where value precision
actually matters.

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
- Windowing's char budget (~4 chars/token) is a proxy nothing enforces
  against a real model's context limit yet — Stage 5b replaces it with
  actual tokenizer counts once a model is pinned.
- No real backend is wired: `build_client` raises `NotImplementedError`
  for `llama_server`/`ollama`. `config/default.toml` still has `TBD`
  placeholders for model repo/revision/digest.
- Retry count, backoff base, and circuit-breaker threshold are hardcoded
  `ResilientLLMClient` defaults (3 retries, 1s base, 5 consecutive
  failures), not yet exposed through `config/default.toml`.
- JSON-schema-constrained output isn't enforced anywhere yet — the `fake`
  backend ignores `LLMRequest.json_schema` entirely. Real enforcement is
  backend-specific (Ollama takes it in `format`, `llama-server` in
  `json_schema`, per docs/PROJECT_NOTES.md §4) and belongs to Stage 5b.

## Throughput bottleneck (requirement 5)

Design intent, to confirm once Stage 5b lands: one inference server, one
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
