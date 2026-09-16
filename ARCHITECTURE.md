# Architecture

Status: Stages 0–4 built (skeleton, SQLite schema, inventory + dedup,
hardened extraction, context windowing), plus Stage 5a (LLM client
interface + `fake` backend), Stage 5b (`llama-server` backend + a real,
downloaded, sha256-verified model pin), offline token counting via a
committed `assets/tokenizer.json`, Stage 5c (`ollama` backend, real
public-registry tag pinned and verified), and Stage 6 (extraction schema +
validation/normalisation). Every backend was run for real against the
actual pinned model, not just mocks — requirement 2's "at least two real
backends" bar is met (`llama_server` + `ollama`). Orchestration/resume and
budget enforcement (Stage 7, which will also drive Stage 6's repair-attempt
loop and wire quarantine writes) aren't built yet.

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

**Model pin: `speakleash/Bielik-4.5B-v3.0-Instruct-GGUF`, `Q8_0`
(~5.1GB).** Commit and file sha256 verified directly against the Hugging
Face API when pinning (2026-09-15), not carried over from
`PROJECT_NOTES.md`'s possibly-stale recommendation — confirmed Bielik 3.1
(announced for June 2026) still isn't released, so 3.0 is current, not
stale. Apache 2.0; fits the M1 16GB budget (~9-10GB ceiling for weights +
KV cache); 8K native context (`--ctx-size` is configured to 2048 — see
below, not the model's ceiling). The base, non-GGUF Bielik repo is gated
on Hugging Face; the GGUF repo actually used for the model itself is not.

**`llama-server` binary pin: `ggml-org/llama.cpp` release `b10985`.**
llama.cpp publishes per-commit `b<N>` tags with real binary assets several
times a day; the traditional `vX.Y.Z` releases carry none. No checksums
file is published, so both platform binaries (macOS arm64, Linux x64)
were downloaded once and their sha256 computed locally, not copied from
anywhere.

**`/completion` over `/v1/chat/completions`.** `LlamaServerClient` uses
llama-server's native, flat-prompt endpoint rather than the OpenAI
-compatible chat one: `LLMRequest.prompt` is already a plain string, so
`/completion`'s equally flat `"prompt"` field needs no messages-list
restructuring, and its `json_schema` parameter name matches
`PROJECT_NOTES.md` §4's own wording more directly than the chat endpoint's
nested `response_format.schema`. Verified for real against the running
server with an actual extraction-shaped prompt + schema — the model
correctly returned constrained JSON (`{"doc_type": "invoice"}`) for a
Polish invoice snippet. Chat-template formatting (Bielik uses ChatML) is
left to whoever builds the prompt string (Stage 6/7) — this client is a
transport layer, not a prompt formatter.

**Offline `tokenizer.json`, committed rather than fetched.** The base
Bielik repo that ships this file is gated on Hugging Face — this tool has
no way to authenticate to it non-interactively, so `scripts/fetch_runtime.py`
can never download it for a reviewer running `setup.sh` fresh. Solved by
sidestepping the download entirely: the file is ~3.7MB, small enough to
commit outright (unlike the model, `vendor/`, gitignored), so it now lives
at `assets/tokenizer.json` and every clone has it with zero setup and zero
gated access needed. `src/extractor/tokenizer.py` loads it via the
`tokenizers` library (prebuilt wheels incl. macOS arm64) for exact,
offline token counts — `PROJECT_NOTES.md` §8 Stage 4's *first*-named
option, not the fallback it looked like last session.
`LlamaServerClient.count_tokens`'s `/tokenize` call stays as the backup
for a model swap or a fork that drops `assets/`. `windowing.build_context`'s
optional `count_tokens` parameter accepts either — a real chars-per-token
ratio measured on a sample of the document, replacing
`CHARS_PER_TOKEN_PROXY` for that call only. Nothing wires either counter
through by default yet (Stage 7's job); the proxy remains the default.
One caveat worth naming: the two real counters can differ by a token or
two on the same text (BOS-token handling differs between `Tokenizer.encode()`'s
default and the server's `/tokenize`), immaterial for budget *sizing* but
a reason neither should be treated as an exact oracle if Stage 6 ever
wants precise counts for something other than windowing.

**Backend selection** (config-selectable, per requirement 2): `llama-server`
is the real, working default; `ollama` is the second real backend
(requirement 2's bar is met); `fake` remains for tests. Concurrent
requests to the model is a backend server-slot parameter, not `--workers`;
conflating the two would queue requests past their timeout for no gain.
Starting/stopping either server process is not these clients' job —
`LlamaServerClient`/`OllamaClient` only ever talk to a server already
running; process lifecycle belongs to Stage 7/CLI.

**Ollama pin: the public `speakleash/bielik-4.5b-v3.0-instruct:q8_0`
registry tag, not a locally-made import name.** Verified directly against
`registry.ollama.ai`'s Docker-registry-v2-compatible manifest API (no full
pull needed just to check it): the model layer's digest is
`sha256:562f2291...` — byte-identical to the GGUF already pinned for
`llama_server`, confirming both backends serve the exact same published
weights. `model_digest` in config is the manifest's `ollama-content-digest`
response header, the value a real `ollama pull` by a reviewer would
produce — not something computed locally, which would only be reproducible
by someone importing the identical way. `OllamaClient` was verified for
real regardless: the actual GGUF already downloaded for `llama_server` was
imported into a local Ollama daemon via a Modelfile (`FROM <path>`) instead
of pulling the registry copy again — same weights, zero duplicate ~5.1GB
transfer — and a real request through
`config → build_client → complete()` returned correct schema-constrained
JSON. One real, generally-useful finding from that: a cold-start `/api/generate`
call (first inference after import, loading a 4.5B model on CPU) took
~107s — comfortably past a naive 60s client timeout, which fired and
aborted the load correctly (confirmed by the server's own log:
"client connection closed before llama-server finished loading"). Not a
bug — `LLMTimeout` did exactly its job — but a concrete number for
whatever default per-request timeout Stage 7 ultimately picks.

`OllamaClient` posts to `/api/generate` with `"stream": false` (its
default is streamed NDJSON, which this client — like `LlamaServerClient`
— deliberately doesn't handle, wanting one complete response per call) and
puts the JSON schema in Ollama's `format` field (`PROJECT_NOTES.md` §4:
"Ollama takes a JSON Schema in format"). No `count_tokens` method here,
unlike `LlamaServerClient`: Ollama exposes no `/tokenize`-equivalent, but
doesn't need one — `extractor.tokenizer`'s offline counting is the same
Bielik tokenizer regardless of which server runs it, so it already covers
this backend too.

**Retry/backoff/circuit-breaking is one shared wrapper around every
backend, not per-backend logic.** `ResilientLLMClient` decorates any
`LLMClient` — `fake`, `llama_server`, and `ollama` alike, proven
identically against all three (the same `tests/test_llm_resilience.py`
patterns re-run over `httpx.MockTransport` in both
`tests/test_llm_llama_server.py` and `tests/test_llm_ollama.py`) — with
exponential backoff up to a retry
cap, then a circuit breaker that opens after N *consecutive* failures.
Once open it stays open for the rest of the process — no half-open retry
attempt mid-run;
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

**Context windowing's budget defaults to characters, with tokens as an
opt-in.** Long documents get head (35%) + tail (25%) + budget-bounded
windows around keyword/regex-candidate hits in the middle, so a field
mentioned once, deep in a 286-page contract, still reaches the model —
see the tokenizer decision above for why a real counter is optional
rather than required. Candidate regexes (NIP/date/amount shapes) are
deliberately imprecise — matching decoys too is fine, since windowing only
needs anchor positions; Stage 6 will reuse the same module where value
precision actually matters.

**Stage 6's `validate_extraction()` is a pure function with no model call
of its own.** It parses the model's raw JSON against
`extractor.schema.ExtractedFields` (a Pydantic model whose
`model_json_schema()` also *is* `LLMRequest.json_schema` — one schema
drives both what's requested and what validates the response), normalises
every field, and reports what's wrong. The repair-attempt retry loop that
decides whether to re-prompt the model, null a field, or quarantine the
document is Stage 7's job, since Stage 7 is the only layer already holding
an `LLMClient` — this keeps Stage 6 testable with canned JSON strings, no
`fake` backend needed, matching every other stage's "tests never call a
real model" rule.

**Field-level failures null just that field; only an unparseable
`doc_type`/malformed JSON/empty summary is document-level.** This mirrors
`documents`'s own CHECK constraint (`done ⟹ doc_type is set`) rather than
inventing a second failure taxonomy: a document that can't produce a valid
`doc_type` cannot become `done` and must go through repair-then-quarantine
(`quarantine_reason='llm_invalid_output'`); a single field like
`counterparty_name` failing its check is nulled and the document still
completes normally, matching how routinely a field is legitimately `null`
in a real document.

**Per-field corroboration is not the same literal "occurs in source text"
test for every field** — deliberately, because a literal substring check
is only meaningful for values the model is expected to copy near-verbatim.
`counterparty_name`/`counterparty_tax_id` get the strict check (these are
exactly the two fields `PROJECT_NOTES.md` §8 names as the anti-injection
target: "blocks both hallucination and an injected 'use this other
company's NIP'"), with the NIP variant additionally matching any
differently-formatted-but-same-digits span `find_nip_candidates` finds, not
just an exact string. Dates and amounts are *not* literally source-matched:
`docs/DATA_SPEC.md` §3.3's derived-due-date case ("termin płatności: 14 dni
od daty wystawienia") produces a value that legitimately never appears
verbatim in the source at all, and a normalised `Decimal` like `1234.56`
will never literally equal a source rendering like `1 234,56 zł`. Both are
instead nulled only when the source has no date-/amount-shaped text
whatsoever (reusing `find_date_candidates`/`find_amount_candidates` from
Stage 4) — a weaker, cheaper check, but the only one that doesn't reject
correct extractions.

**Currency validation is an ISO 4217 allowlist plus normalisation of only
the symbols that are unambiguous everywhere — not a re-derivation of the
full seller-context precedence chain.** `docs/DATA_SPEC.md` §3.2's 6-level
precedence (explicit code → unambiguous symbol → ambiguous symbol resolved
by seller country → seller postal address → seller VAT prefix → null) needs
document context (seller address, VAT ID) that a pure post-hoc validator
doesn't have; that reasoning belongs to prompt/extraction design in Stage 7.
What Stage 6 does, per `PROJECT_NOTES.md` §8's literal wording, is narrower:
`zł`→PLN, `€`→EUR, `£`→GBP (genuinely unambiguous everywhere) plus the
ISO 4217 allowlist itself. **A bare `$` is deliberately never auto-mapped to
USD.** `docs/DATA_SPEC.md` §3.2 classifies `$` as an *ambiguous* symbol
(USD, CAD, AUD, NZD, HKD, SGD, MXN, ...) resolved only by seller-country
context Stage 6 doesn't have — its own worked example is `$` + a Toronto
address → CAD, not USD. The real ground truth confirms this: the corpus's
`Pump_replacement_estimate.eml` record (tagged
`currency-dollar-no-signal-null` in `data/MANIFEST.md`) mentions "$500" in
prose but has no seller address/VAT signal, and its expected `currency` is
`null`. Guessing USD from a bare `$` would fail that exact, real, committed
case. Also confirmed by the same record: **an amount without a resolvable
currency gets nulled too**, not just the currency field —
`validate_extraction` applies that as a general rule, not a one-off.

**A failed NIP mod-11 checksum is informational only, never a rejection
reason.** `nip_checksum_valid()` (weights 6,5,7,2,3,4,5,6,7,
`PROJECT_NOTES.md` §8) exists to be surfaced by a future consumer (e.g.
Stage 8's report), not to null or reject the value itself — confirmed
against the real ground truth: `Faktura_FV_2024_09_019.docx` (tagged
`nip-checksum-fail`) has a real, populated `counterparty_tax_id` in
`data/expected.jsonl` despite failing the checksum.

**Known, stated limitation: the occurs-in-source check is necessary but not
sufficient against a fake-JSON-block injection.** `docs/DATA_SPEC.md` §5's
injection corpus includes documents with a plausible-looking alternate
JSON block embedded in the text, describing a *different* document (its own
NIP/name/amount). Those decoy values technically "occur in the normalised
source text" too — `occurs_in_source` cannot by itself tell genuine business
content from an embedded decoy, and `tests/test_validation.py`'s
`test_fake_json_injection_value_still_occurs_known_limitation` asserts this
honestly rather than papering over it. The actual defence against that
injection class is structural, not content-based: `extractor.db`'s
single-row-scoped parameterised write (one document's content can only ever
affect its own row), which is what `docs/DATA_SPEC.md` §5 actually asks the
test suite to verify ("no record other than that document's own was created
or modified") — Stage 6's job is only to catch values that don't appear
*anywhere* in the source at all.

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
- Windowing's char-budget proxy (~4 chars/token) is still the *default* —
  both real counters (offline `tokenizer.py`, live `/tokenize`) exist and
  are tested, but nothing calls `build_context` with either yet. That
  wiring — almost certainly the offline one, now that it costs no network
  round trip per document — is Stage 7's call.
- Retry count, backoff base, and circuit-breaker threshold are hardcoded
  `ResilientLLMClient` defaults (3 retries, 1s base, 5 consecutive
  failures), not yet exposed through `config/default.toml`. Same for both
  clients' 60s/80s-ish per-request timeouts — and the real ~107s Ollama
  cold-start measured this stage suggests the eventual default needs to be
  well above a naive guess, or Stage 7 needs a separate, longer "model
  loading" timeout distinct from the steady-state per-request one.
- Neither `llama-server`'s nor Ollama's process/model lifecycle (start with
  the pinned model, health-check before sending traffic, stop on exit) is
  managed by this tool yet — both clients only ever talk to a server
  already running. Verified manually both times (started each by hand, ran
  a real request through the full `config → build_client → complete()`
  pipeline); Stage 7/CLI needs to automate that start.
- The model startup digest check `PROJECT_NOTES.md` §6 describes (compare
  what's actually present against `config/default.toml`, refuse to run on
  mismatch) doesn't exist for either backend yet — `fetch_runtime.py`
  writes `vendor/versions.lock` for `llama_server` but nothing reads it
  back; nothing queries Ollama's local `/api/tags` digest against the
  pinned one either.
- `fetch_runtime.py` still only fetches for `llama_server` (GGUF + server
  binary) — it never runs `ollama pull` for the Ollama pin. A reviewer
  who switches `config/default.toml` to `ollama` needs to pull the model
  themselves; `setup.sh` doesn't do it for them. Deliberately out of scope
  here (fetching both backends' models unconditionally would slow down
  every setup for whichever backend isn't the active default) but worth
  flagging as unaddressed.
- `validate_extraction()` is not wired into anything yet — nothing calls
  it with a real (or `fake`) LLM response, and nothing writes its output
  into `documents` or maps `needs_repair=True` to an actual repair prompt
  or `quarantine_reason='llm_invalid_output'`. That orchestration loop —
  call the model, validate, retry once on `needs_repair`, then null/
  quarantine — is Stage 7's.
- The ISO 4217 allowlist is a fixed, hand-maintained set of active
  alphabetic codes. It is not sourced from a machine-readable registry, so
  a currency that's added, split, or redenominated after this was written
  (rare, but it happens — see PLN's own 1995 redenomination) would need a
  manual update.
- `occurs_in_source`'s date/amount corroboration is weak by design (see
  above): it confirms the source contains *some* date-/amount-shaped text,
  not that the model's specific value matches anything found there. A
  wholesale-fabricated-but-plausible amount on a document that legitimately
  discusses a different amount would not be caught by this check alone.

## Throughput bottleneck (requirement 5)

One inference server, one GPU. Prompt processing is compute-bound, so
parallel server slots barely help beyond a small number — near-sequential
throughput should be assumed. The dominant lever on latency is context
size sent to the model, not model size, which is why Stage 4 windows long
documents instead of sending full text. `--workers` controls concurrency
for everything *except* the model call; it doesn't increase model
concurrency, a separate backend-side slot count.

The request/response plumbing itself is confirmed, not just designed: a
real request against the real pinned model, through the full
`config → build_client → complete()` path, returned correct
JSON-schema-constrained output. Wall-clock throughput against the M1
budget (20 min / ~40 docs) is *not* confirmed — this dev environment is
x86_64 Linux, CPU-only, a different machine entirely; the M1's actual
numbers need `PROJECT_NOTES.md` §5's estimation approach or real hardware.

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
