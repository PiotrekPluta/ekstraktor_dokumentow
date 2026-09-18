# Architecture

Status: Stages 0–6 built (skeleton, SQLite schema, inventory + dedup,
hardened extraction, context windowing, LLM client + `fake`/`llama_server`/
`ollama` backends, extraction schema + validation/normalisation), **Stage 7
(orchestration, resume, budget)**: `extractor run` is now the real command —
claim pending documents in deterministic order, build a windowed+prompted
request, call the model, validate, repair-once-then-quarantine, write the
result, all resumable after `SIGKILL` and bounded by `--limit`/`--budget`.
Verified for real against the pinned `llama-server` + Bielik model, not
just the `fake` backend (see "Stage 7 real-fixture verification" below) —
this is also where a real, load-bearing bug was found and fixed: see the
context-budget decision below. Plus **Stage 8 (`report`/`eval`)**: both
commands are real, computed purely from `SELECT`s over the db (see "Report
and eval" below) — no third command remains a stub. Plus **Stage 9 (test
suite)**: requirement 10's acceptance list (resume-after-`SIGKILL`, budget
stop, report balance, an unprocessable document, injection, `--workers 1`
vs `16`, memory on a huge file) is now covered end to end, including a
real `SIGKILL` against a real spawned process, not just in-process
simulation (see "Test suite" below).

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
`PROJECT_NOTES.md` §8) exists to be surfaced by a future consumer, not to
null or reject the value itself — confirmed against the real ground truth:
`Faktura_FV_2024_09_019.docx` (tagged `nip-checksum-fail`) has a real,
populated `counterparty_tax_id` in `data/expected.jsonl` despite failing
the checksum. Stage 8 decided **not** to surface it as a `report`
diagnostic after all: `nip_checksum_valid()` is Polish-NIP-shaped (exactly
10 digits) and returns `False` for anything else, so a naive "how many
`done` documents fail the checksum" count would mostly just count foreign
counterparties' VAT IDs — the corpus has plenty (`docs/DATA_SPEC.md`'s
non-Polish counterparties) — as false "failures", which would be worse
than not reporting it at all. Doing this correctly needs classifying
tax-id shape/country first; still unwired, listed under "Known
limitations".

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

**Stage 7: `--limit`/`--budget` are cumulative across the whole db's
history, not reset per `run` invocation.** The only reading consistent with
requirement 4's "record set after any number of interruptions matches an
uninterrupted run" holding unconditionally — three interrupted runs at
`--limit 5` must not process 15 documents. `--limit` is enforced
structurally: `orchestrate.run()` computes the target-id list once, up
front, from `SELECT id FROM documents WHERE status='pending' ORDER BY id
LIMIT (limit - already_attempted)`, where `already_attempted` is
`COUNT(done) + COUNT(quarantined AND quarantine_reason='llm_invalid_output')`
over the *entire* db. That query result is fixed before any worker starts,
so the set of documents attempted this run cannot depend on `--workers` or
on how many of those claims later fail and go back to `pending`.
Inventory-time quarantines (`corrupt_file` etc.) never reached the LLM step
and don't count against the limit. `--budget` gets the same cumulative
treatment for free: `token_ledger.reserved_tokens` already accumulates
across every `run_id`, so "spend so far" is `SUM(reserved_tokens)` over the
whole table, checked live by a shared `_Accountant` before every
reservation — enforced at runtime rather than pre-sized into the target
list, since token cost depends on content in a way document *count*
doesn't.

**`documents.source_text` (Stage 7 addition to Stage 2/3's schema and
`inventory.py`).** The original, pre-`normalize_text()` extracted text is
now stored once at inventory time and reused by windowing/validation,
rather than re-extracting the file a second time when orchestration needs
it — the "100x scale" section below already flags per-file extraction
overhead as a real cost; doing it twice would double it for nothing. `NULL`
exactly when `identity_kind='bytes'` (quarantined before any text existed).

**Threads, not processes, for the orchestration loop.** Per-document work
here is dominated by an HTTP call to the model server (I/O-bound), unlike
Stage 3's CPU/C-library-bound extraction subprocesses that need real
process isolation to kill a hung pypdfium2 call. One `ResilientLLMClient`
(and its one `CircuitBreaker`) is shared across every worker thread — a
single "backend is down" signal, not one breaker per worker that would each
independently need `failure_threshold` failures to notice the same outage.
This made `CircuitBreaker`'s previously-unguarded state
(`_consecutive_failures`/`_open`) a real concurrency bug once Stage 7 became
the first caller to invoke `complete()` from multiple threads: fixed with a
`threading.Lock` around each read-modify-write, verified by a dedicated
20-thread concurrent-failure test (`test_concurrent_complete_calls_trip_
breaker_exactly_once_no_race`) that would flake under a lost-increment race
without the lock.

**`llm_invalid_output` is reused for "no usable response after retries",
not a new quarantine reason.** Two paths lead there: the model responded
but the JSON stayed unusable even after one repair attempt, or
`ResilientLLMClient.complete()` raised after exhausting its own retries
without tripping the breaker — a document-scoped failure distinct from a
whole-run `backend_unavailable` event. Both mean the same thing from the
document's point of view: this document did not get a valid extraction.
**A found, worth-naming edge case**: with a tight `failure_threshold`, the
*specific* document whose attempt happens to be the one that pushes
consecutive failures over the threshold correctly gets `backend_unavailable`
and is released to `pending` — but a document that failed just *before*
that point, whose own retry budget was exhausted first, can get individually
quarantined as `llm_invalid_output` even though the underlying cause was the
same backend outage. `ResilientLLMClient` has no way to know in advance
which failure will be the last straw, so this is real, not merely
hypothetical (`tests/test_orchestrate.py`'s backend-unavailable test had to
be tuned — `max_retries >= failure_threshold` — specifically to avoid
hitting it and assert the whole-run path instead). Not corrected: the
alternative (treating every failure as potentially breaker-adjacent and
never quarantining) would mean one persistently-malformed document could
never be quarantined at all as long as the backend stays up. Stated here
rather than silently accepted.

**Token reservations stay conservative forever, never released.** A
`token_ledger` row is inserted *before* every model call (one row per
attempt: the initial call is `attempt=1`, a repair call is `attempt=2`,
`UNIQUE(run_id, document_id, attempt)`). A process killed between the
reservation and the response leaves `tokens_in`/`tokens_out`/`completed_at`
all `NULL` — and it is never backed out: `SUM(reserved_tokens)` counts it
forever, matching requirement 6's "the tool ends *before* the budget would
be exceeded" literally rather than trying to detect and roll back an
abandoned attempt.

**Prompt design (`extractor/prompt.py`): ChatML, and the JSON schema is
deliberately *not* embedded in the prompt text.** The first version did
embed `ExtractedFields.model_json_schema()` inline (per the original plan),
and a real-fixture run against the pinned `llama-server` (CLAUDE.md's
"verify against real fixtures" rule) immediately caught why that was wrong:
the schema alone costs 500-1300 tokens depending on formatting, and the
full system prompt with it embedded came to ~2200 tokens — already over the
server's pinned 2048-token `--ctx-size` *before a single token of document
context or of the response*. Every request was rejected outright
(`request (2400 tokens) exceeds the available context size (2048 tokens)`).
Fix: the schema is only ever sent structurally, via `LLMRequest.json_schema`
(which `LlamaServerClient`/`OllamaClient` already forward as the backend's
own grammar-constrained-decoding parameter — confirmed in the real
request's `generation_settings.grammar` field, a real GBNF grammar compiled
from the schema); the prompt text only lists the eight field names and
explains their *meaning* in prose (seller-not-buyer, the six-level currency
precedence chain, derived-due-date reasoning) — the model doesn't need to
see the raw schema twice to know what to fill in and how to name it.

**`schema.py`'s `gross_amount` uses a hand-written json-schema pattern
(`_GROSS_AMOUNT_SCHEMA`), not Pydantic's own `Decimal` pattern.** Found the
same way, against the same real `llama-server`: since the schema is sent
structurally as grammar (previous paragraph), `llama-server` logs "JSON
schema conversion was incomplete: pattern `^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$`
is not supported (unsupported group syntax), accepting any string" for
Pydantic's stock Decimal pattern — llama.cpp's schema-to-GBNF converter
doesn't support regex lookahead, so it silently drops the character-set
constraint for that `anyOf` branch, meaning the grammar would let the
model emit *any* string there, not just digit/sign/dot shapes (still
caught downstream by `validation.py`'s `Decimal` parse, just later and
noisier than necessary). Fix: `WithJsonSchema` overrides that one branch
with a lookahead-free pattern of the same semantics (rejects sign/dot-only
degenerates like `"-"`/`"."`/`""`, same as the lookahead did) — and avoids
`\d` too, which this llama.cpp build's converter also rejects
("unsupported escape"); `[0-9]` only. Verified against `vendor/llama-server`
directly: the warning reproduces with the raw Pydantic schema and is gone
with the replacement (`tests/test_schema.py` pins the pattern's shape, not
the live warning, since the test suite may run without any backend
available per CLAUDE.md's "no model/network/API key" rule).

**`orchestrate._context_window_budget()`: the windowed-context token budget
is computed per run, not hardcoded.** Directly downstream of the bug above:
even after dropping the embedded schema, the system prompt is still ~1000
tokens fixed overhead, and a repair turn adds the previous response
(≤`max_output_tokens`) plus correction instructions on top. Sizing
`windowing.build_context`'s `token_budget` off `windowing.DEFAULT_TOKEN_BUDGET`
(1500) unconditionally would still blow a 2048-token `--ctx-size` once that
overhead is added. Instead: `context_tokens` (from
`config.raw["backend"][backend]["context_tokens"]`, defaulting to a large,
effectively-unconstrained value for `fake`) minus the larger of
`count_tokens(build_prompt(""))` / `count_tokens(build_repair_prompt("", "", ""))`
minus `2 * max_output_tokens` (one for the repair turn's echoed-back
previous response, one for the new completion) minus a small safety margin,
floored at 200 tokens. Computed once per `run()` call (it depends only on
config and the counter, not on any document) and threaded through to every
worker/document alongside `count_tokens` itself.

**Found via the same real-fixture pass, fixed by `extractor.llm.lifecycle`
(below), not at the time it was first found**: `llama-server`'s
`--ctx-size` is the *total* KV-cache budget shared across all of its
parallel slots when `kv_unified=true` (its default), not a per-slot
budget. Starting the server with `--ctx-size 2048` and its default
`--parallel` (4) means each of the 4 concurrently-served requests only
really has ~512 tokens of headroom, not 2048 — confirmed for real: four
`--workers`-driven concurrent requests, each individually well under 2048
tokens, still failed mid-decode with `Context size has been exceeded` once
combined. `config/default.toml`'s `context_tokens` is a single number and
`_context_window_budget()` (above) assumes it's what one request actually
gets. This only reproduces under real concurrency (`--workers > 1` against
a real backend with more than one server slot) — every automated test uses
the `fake` backend, which has no concept of a shared KV cache, so nothing
in the test suite catches this on its own.

**`extractor.llm.lifecycle` starts and health-checks `llama-server` itself
now**, closing the gap the paragraph above left open and the one below
("Neither `llama-server`'s nor Ollama's process/model lifecycle... is
managed by this tool yet") used to describe. Two callers, both going
through the same `ensure_running()`:
`extractor.cli.run` calls `ensure_backend_ready()` before every run
(self-healing — a `GET /health` first, and a new process only if that
doesn't answer 200); `scripts/smoke_test_backend.py`, run at the end of
`setup.sh`, calls the same function once at install time and, on success,
leaves the server running — this is what makes `./setup.sh` actually start
"the whole environment" rather than only fetch files for it, and also
sends one real, tiny completion through the freshly-started server, since
`fetch_runtime.py`'s checksum verification can only prove the *bytes* are
right, not that the GGUF actually loads or that this llama-server release
can serve it. Found and fixed together, real fixture again: this session's
manually-started `--ctx-size 8192 --parallel 4` on a CPU-only dev box still
tripped `stop_reason=backend_unavailable`, traced to `n_threads` and
`timeout_s`, not `--ctx-size`, showing the previous fix already held — see
the `timeout_s` limitation entry below.

Because `lifecycle.py` is now the thing starting the process, it always
sets `--parallel` to the same `n_slots` (`config.workers`) it multiplies
into `--ctx-size`, so the two can no longer drift apart the way a
human-started server previously could. `is_healthy()` is checked *before*
ever starting a process, so a server started by hand (any `--parallel`,
any extra flags) is left alone and used as-is — deliberately no opt-out
flag, since the health-check-first behaviour already is one. `-ngl`/`-t`
are deliberately never guessed from `platform.system()`: `PROJECT_NOTES.md`
§5 notes GitHub Actions' own `macos-14` runners are arm64 but Metal
Performance Shaders don't work under Apple's virtualisation there, so
`Darwin`+`arm64` alone doesn't tell you whether Metal offload is safe to
enable — a real M1 Mac and that CI runner need opposite answers on the
same OS/architecture pair. `[backend.llama_server].extra_args` (an
optional list of raw argv strings, empty by default so `config/
default.toml`'s M1-target behaviour is unchanged) is the escape hatch for
whichever flags a given environment actually needs instead of a baked-in
guess that would be wrong for one of the two; `my_run_testing/
local_cpu.toml` (untracked, this session's CPU-only dev config) sets
`extra_args = ["-ngl", "0"]` explicitly rather than relying on this
binary's being CPU-only anyway.

**Two GitHub Actions workflows run this project's code on real macOS
arm64** (GitHub's `macos-14` runner is an M1) — correctness only, never
performance, per the same GitHub-arm64-has-no-working-Metal fact the
`extra_args` decision above cites. Deliberately two *files*, not one file
with two jobs (the original shape): `macos.yml` (`pytest-macos-arm64`, the
`fake`-backend suite, no network/model, on every push/PR — cheap) and
`macos-smoke-e2e.yml` (`smoke-e2e-macos-arm64`, a real `./setup.sh` +
`extractor run --limit 1` against the actual pinned Bielik model,
`workflow_dispatch`-only since it downloads the real ~5GB GGUF every
time). Splitting them was a deliberate correction, not a rename: a single
`on: [push, pull_request, workflow_dispatch]` workflow with the heavy job
merely gated by `if: github.event_name == 'workflow_dispatch'` still
*runs the workflow* (with that job skipped) on every push/PR — a separate
file whose own `on:` is only `workflow_dispatch` means nothing in it
executes at all until someone triggers it by hand from the Actions tab.
The second workflow needs `-ngl 0` and a taller `timeout_s` than
`config/default.toml` sets, but can't just edit `default.toml` (that stays
the untouched M1-target default) — `config/ci_macos.toml` (committed, CI
-only) pins the identical model/binary and adds only those two overrides.
`scripts/smoke_test_backend.py` gained an `EXTRACTOR_CONFIG_PATH` env-var
override (defaults to `config/default.toml`, unset for a real reviewer's
plain `./setup.sh`) so the workflow can point it at `config/ci_macos.toml`
without a second copy of the script or a reviewer-facing CLI flag nobody
else needs. `fetch_runtime.py` itself takes no such override — it only
ever fetches the one pinned model `config/default.toml` names, which is
also what `config/ci_macos.toml` names, so there is only ever one
`vendor/versions.lock` to produce or read regardless of which config a
given step runs with.

**The `pytest-macos-arm64` job's very first real run found a genuine,
platform-independent bug**: `setup.sh` ran `uv sync --locked` with no
`--group`/`--all-groups` flag, which only installs the `dev` dependency
group (uv treats a group literally named `dev` as included by default) —
not `datagen` (Pillow, `reportlab`), which `tests/test_generator.py`
imports transitively (via `scripts/generate_data.py` →
`scripts/generator/corrupt.py`'s `from PIL import Image, ImageDraw`) at
*collection* time, aborting the entire suite (`Interrupted: 1 error during
collection`) before a single test ran. This had been latent since Stage 1:
every local dev `.venv` used while building this project already had
`datagen` installed from that stage's own work, so `uv run pytest` never
exercised the bare-`dev`-group path `setup.sh` actually produces on a
fresh clone — reproduced locally on Linux too once checked (`uv sync
--locked` alone genuinely uninstalls Pillow/reportlab here), confirming
it was never OS-specific; the CI job just happened to be the first truly
fresh `uv sync` this project had run. Fixed by changing `setup.sh` to
`uv sync --locked --all-groups`, so a fresh reviewer clone installs
everything both `pytest` and `ruff` need, matching `PROJECT_NOTES.md` §6's
own stated intent ("do install it in `setup.sh` — requirement 10 has the
reviewer run the tests") literally rather than only for the `dev` group.
The first attempt at this fix touched only `setup.sh` and still failed on
the next CI run, for a reason specific to this workflow rather than the
underlying bug: `pytest-macos-arm64` deliberately never calls `setup.sh`
(job 1 is the cheap job — no model fetch), so it has its own, separate
`uv sync --locked` line in `.github/workflows/macos.yml` that needed the
same `--all-groups` flag applied independently.

**The next `pytest-macos-arm64` run (after the `datagen`-group fix) found
two more real, previously-latent bugs**, both fixed in this same session:

- `tests/test_memory.py`'s `_ru_maxrss_kb()` returned `ru_maxrss` raw and
  unconverted. Its own module docstring already named the fact this
  invited ("`ru_maxrss` alone differs in units between Linux and
  macOS") but the helper itself never actually applied the conversion —
  on macOS `ru_maxrss` is bytes, not kilobytes, so the test reported a
  spurious ~6,300,000 "KB" (~6GB) of subprocess growth for what a `//
  1024` fix (guarded by `sys.platform == "darwin"`) shows is actually
  ~6MB — comfortably under the 150MB canary, i.e. streaming was working
  correctly the whole time; only the unit label lied.
- `tests/test_inventory.py`'s `corpus_conn` fixture asserted against the
  full 48-file corpus, but `data/corpus/huge_log_*` are git-ignored
  (DATA_SPEC.md §6 — a several-hundred-MB file has no business being
  committed) and only ever existed on this project's dev machines because
  `scripts/make_huge_file.py` had been run there by hand at some earlier
  stage and simply left its output on disk — exactly the same shape of
  bug as the `datagen`-group gap above (a real fresh checkout was the
  first thing to ever *not* have it). Fixed the same way `run`'s backend
  self-heals (the "Key decisions" `extractor.llm.lifecycle` entry above):
  `corpus_conn` now calls `_ensure_huge_fixtures_exist()` first, which
  regenerates both files via `make_huge_file.main()` only if they're
  missing. Safe to call on a repo that already has the committed
  `data/expected.jsonl` rows for them — `make_huge_file.append_to_expected`
  already skips any file already listed there, so this never duplicates
  those two ground-truth rows. Verified for real: deleted both files from
  this session's own checkout, reran `test_inventory.py`/`test_memory.py`
  cold, confirmed regeneration produces byte-identical sizes (314572860 /
  31457322) and `data/expected.jsonl`/`data/MANIFEST.md` stay unmodified
  (`git status` clean on both).

Both were only ever reachable from a genuinely fresh checkout — no local
dev `.venv`/`data/corpus` here had been "fresh" since Stage 1/2, which is
exactly why the `pytest-macos-arm64` CI job (not reasoning about the code)
is what found them.

**A third, more consequential bug from that same run: `scripts/generator/
render_pdf.py`'s `ensure_font_registered()` only ever looked at Linux font
paths** (`/usr/share/fonts/.../DejaVuSans.ttf`), so every PDF-rendering
path in `test_generator.py` raised `RuntimeError: DejaVu Sans font not
found` on the CI runner. Unlike the two bugs above, this one is not
CI-only: **the actual evaluation machine is macOS** (`docs/ZADANIE.md`
"Maszyna oceniająca"), which doesn't ship `fonts-dejavu-core` either, so
this would have failed requirement 10's test suite there too, not just in
CI. Fixed by vendoring the font rather than depending on any system
package manager: `scripts/generator/assets/DejaVuSans.ttf` /
`DejaVuSans-Bold.ttf` (plus `DEJAVU-LICENSE`, Bitstream Vera License,
explicit free-redistribution terms), fetched from the upstream
`dejavu-fonts` GitHub release (`version_2_37`) and checked *before* the
old system paths, which stay as a harmless fallback. Deliberately not
"add macOS system-font paths instead" (the option this session considered
and rejected): this sandbox has no way to confirm what font paths exist
on a real M1, nor whether a substitute has full Polish-diacritic coverage
— a silently-wrong-but-present font would be worse than a loud
`RuntimeError`, and DejaVu was originally chosen specifically for its
Polish glyph coverage. Verified for real, not just "registration
succeeds": rendered a paragraph containing every Polish diacritic
(`ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ`) through `render_pdf()` with the font-candidate
list forced to *only* the vendored path (no system font reachable at
all), then read it back with `pypdfium2` — the same PDF library
`extractor.textextract` itself uses — and confirmed the diacritics
survive the full render-then-extract round trip.

**`extractor.llm.lifecycle.wait_until_healthy()`'s timeout error now
includes the last 40 lines of `llama-server.log`** — found missing when
`macos-smoke-e2e.yml`'s first real run hit exactly this timeout with
nothing else to go on: `start_process()` redirects the server's own
stdout/stderr straight to that log file, never to whichever process
called `ensure_running()`, so a CI step (or a `run` invocation) that
doesn't separately `cat` the file got only "didn't become healthy",
not *why*. `ServerLifecycleError`'s message is what both
`extractor.cli.run`'s self-healing and `scripts/smoke_test_backend.py`
ultimately print, so this fixes the diagnostic gap for both callers from
one place rather than adding a log-printing step to the workflow only.
Whether that first real timeout is model-loading being genuinely slower
on a shared, 3-core `macos-14` runner, or `llama-server` (even with
`-ngl 0`) still probing the Metal backend at startup under a VM where
Metal Performance Shaders don't work (`PROJECT_NOTES.md` §5 — the same
fact the `extra_args` decision above cites) is not yet known; this is
what the next run's log tail is for, before raising `startup_timeout_s`
on a guess.

**That next run's log tail ruled out a clean crash, at least**: it showed
`load_model: loading model '...'` and then nothing else for the full 60s
— no error, no stack trace, and also none of the near-instant GGUF-header
metadata lines (`print_info: ...`) that normally follow within
milliseconds of that line on a working load, which reads as either a
genuinely slow first read on this runner's disk, or a stall very early in
opening/mmapping the 5GB file, not a mid-load slowdown. Two follow-ups,
both to test that "slow, not stuck" hypothesis before assuming a hang:

- `startup_timeout_s` (optional, under `[backend.llama_server]`, same
  absent-means-unchanged pattern as `timeout_s`/`extra_args`) makes the
  `/health`-wait window itself configurable — a different axis from
  `timeout_s` (a single completion request, once already serving).
  `config/ci_macos.toml` raises it to 600s as the experiment; if it still
  times out even there, that points at an actual stall rather than
  slowness.
- `wait_until_healthy()` gained an optional `on_waiting(elapsed_s)`
  callback, fired roughly every 15s while still waiting (not on by
  default — `extractor.cli.run`'s self-healing stays quiet;
  `scripts/smoke_test_backend.py` opts in). Without it, a multi-minute
  wait produces zero output in between "ensuring llama-server is up..."
  and either success or the timeout, which is indistinguishable from a
  hung CI job from the outside.

**`FakeLLMClient` gained a `responses: list[LLMResponse]` sequencing mode**
(returns them in order, then repeats the last) — needed to test the
repair-attempt path (invalid JSON, then valid) without a second test
double, and made thread-safe (`self.calls` appended under a lock) since
Stage 7 is the first stage to share one `FakeLLMClient` across worker
threads, the same way a real backend client would be.

**Requirement 9 (2GB peak memory) is a design property, not enforced at
runtime.** `ThreadPoolExecutor(max_workers=config.workers)` bounds how many
documents' `source_text` and windowed context are ever held in memory at
once to `--workers`, never the whole corpus — Stage 2/3 already streams
input and caps extraction per file (`MAX_TEXT_CHARS`, `MAX_ENTRY_BYTES`), so
Stage 7 doesn't undo that by loading everything up front. No new
memory-limiting code was added; the brief tests this empirically at
Stage 10 via `/usr/bin/time -l` on the real eval machine, not via an
in-tool enforcer. "The process" is read as the whole process tree (main +
any subprocess workers, per Stage 3's extraction subprocesses) — the
conservative reading of an ambiguity `PROJECT_NOTES.md` §3 leaves open.

**CLI**: `--workers`/`--limit`/`--budget` override `config/default.toml`'s
`[run]` defaults when explicitly given (`--workers`'s CLI default changed
from a hardcoded `4` to `None`, so "not given" is distinguishable from "given
as 4"); when omitted, the config file's values apply. `extractor run` now
does exactly what `docs/ZADANIE.md` §3 says — "process INPUT into DB,
resuming any previous run found there" — as two steps in one command:
`inventory.build_inventory` (idempotent, safe to redo) then
`orchestrate.run`, both against the same db, via `extractor.db.connect` and
`extractor.llm.build_client`.

### Report and eval (Stage 8)

**`report` is pure `SELECT`s over `documents`/`files`/`runs`/`token_ledger`,
never in-memory counters accumulated during `run`.** That is the entire
mechanism behind "the report balances" (requirement 7): `duplicate_files`
is *defined* as `input_files - unique_documents` rather than measured
independently, and `unique_documents = processed_ok + quarantined +
not_started` holds because `documents.status` is a `CHECK`-constrained enum
covering exactly those buckets — there is no code path where the four
numbers could disagree, including reading a db `SIGKILL` interrupted
mid-run.

**`in_progress` counts as `not_started`, not as a fourth bucket**, matching
`orchestrate._reset_in_progress`'s own treatment of it as "can't tell
whether the model call happened, so not done" on the next resume. A
`report` read concurrently with a live `run` sees a claimed-but-unresolved
row as not-yet-started, consistently with what a resume would do to it.

**Per-run pricing is resolved once, at `run` time, and frozen onto the
`runs` row** (`price_input_per_million`/`price_output_per_million`, new
columns), rather than `report` re-reading `[pricing.<backend>]` from a
config file. `docs/ZADANIE.md` §3 gives `report` only `--db [--json]` — no
`--config` — so `estimated_cost` has to be reconstructible from the db
alone; freezing the rate per run also means a db that spans runs against
different backends (e.g. resumed after switching `config/default.toml`)
still costs each token at the rate that was actually active when it was
spent, not at whatever the db happens to be pointed at now.

**`stop_reason`/`backend`/`model` describe the most recent `runs` row.** If
that row has no `ended_at`, the process never reached `orchestrate.
_finish_run` — almost always a `SIGKILL` — and `report` synthesises
`stop_reason = "interrupted"` even though the column itself is `NULL`
(`runs.stop_reason`'s own `CHECK` already allows this literal;
`orchestrate.py` just never gets the chance to write it — the run that
would need it is the one that got killed). `wall_time_s` for that same row
falls back to the latest `token_ledger` timestamp for it (`completed_at`,
else `reserved_at`) minus `started_at` — a lower bound, since the true
kill time is unrecoverable, not an exact figure. A run killed before
reserving a single token contributes 0.

**`tokens_in`/`tokens_out` sum only `token_ledger` rows with `completed_at`
set.** A committed reservation for a call that never got a response is
real budget consumption (must never be re-spent) but not real usage, and
requirement 7 asks for usage, not reservations.

**`eval` normalises independently of `extractor.normalize`/`validation`.**
The db's own field values already passed through those once, before ever
being written — reusing them again in `eval` would mean a bug in a shared
normaliser cancels itself out of the score instead of showing up as a
mismatch (the same reasoning `docs/DATA_SPEC.md`'s R0.2 applies to keeping
the generator independent of the extractor). `eval.py`'s normalisers are
small and re-derived from `docs/DATA_SPEC.md` §8's `expected.jsonl` format
rules directly.

**A document not found, still pending, or quarantined counts as wrong for
every non-null expected field — never excluded from the denominator.** The
alternative (score only documents the tool actually finished) would let a
tool improve its accuracy number by quarantining more, which is backwards:
`docs/ZADANIE.md` requirement 7 already says quarantine is not a failure of
the *tool*, but it is absolutely still a failure to *extract that field*,
and `eval` measures extraction. `not_found_in_db` is reported separately
and means something narrower: the expected document's paths don't resolve
to any `files` row at all — normally zero for a full run over the archive
`expected.jsonl` describes (`inventory.build_inventory` registers a row for
every discovered path regardless of outcome); nonzero means `eval` is being
pointed at a different or partial archive. Confirmed against the real
corpus: `not_found_in_db == 2`, exactly the two git-ignored `huge_log_*`
fixtures absent from this checkout (`scripts/make_huge_file.py` not run) —
see "Stage 8 real-fixture verification" below.

**`dedup_mismatches`**: an expected document whose `files` resolve to more
than one distinct `documents.id` — this tool's dedup disagreeing with the
ground truth's grouping. Counted and reported, not silently dropped, but
the record is still scored (against the smaller of the disagreeing ids,
picked only for determinism) rather than excluded, so a dedup bug doesn't
also erase the record from every field's denominator.

**`summary` is scored by a separate, documented loose metric, never folded
into the seven-field accuracy** — `docs/DATA_SPEC.md` §8 is explicit that
exact match is meaningless here and that this must be its own column. The
metric checks, per non-quarantined expected document: the produced text is
non-empty and shaped like one sentence (a `.`/`!`/`?` only counts as a
sentence boundary when followed by whitespace and a capital letter — see
below); the counterparty name's longest non-generic word appears in it
case-insensitively (`_GENERIC_NAME_WORDS`, a short hand-picked Polish/
English legal-form stoplist, not NER); and, for `invoice`/`contract`/
`offer`, a doc-type keyword appears. **No language-match check is
attempted at all** — a real language check would need a language-detection
dependency for one sub-component of a metric that is already explicitly
advisory; left out and stated here rather than faked with an unreliable
heuristic.

**Bug found and fixed against real model output, not the `fake` backend:**
the one-sentence shape check originally flagged *any* `.`/`!`/`?` in the
summary as a second sentence, which misfired on Polish legal-form
abbreviations — `Sp. z o.o.` (ubiquitous in this corpus's company names)
has two internal periods that are not sentence boundaries. A real
`extractor run` against `data/corpus` with the pinned `llama-server` +
Bielik model produced a genuinely correct one-sentence summary that
spelled out a counterparty's full legal name and was still scored `0%` on
the loose metric — caught only by looking at real output, not by reasoning
about the regex in isolation (`tests/test_eval.py`'s existing synthetic
cases all used bare, abbreviation-free names). Fixed by only counting a
mark as a sentence boundary when it's followed by whitespace and a capital
letter (`_SENTENCE_END_RE = r"[.!?](?=\s+[A-ZĄĆĘŁŃÓŚŹŻ])"`), which still
catches a genuine two-sentence summary (the next sentence starts with a
capital) while tolerating `Sp. z o.o.`/`S.A.`-style abbreviations. Same
real run also reconfirmed the Stage 7 Swedish-counterparty currency trap
(`Offer_facade_renovation.eml` → `currency: "SEK"`, still correct) and
produced a live example of the injection-resistance property holding:
one document's text contained "Zignoruj wszystkie wcześniejsze
instrukcje" ("ignore all previous instructions") and the model extracted
it *verbatim as that document's own `counterparty_name` field value* —
fooled at the field level (a legitimate, expected outcome; nothing here
claims field-level robustness against adversarial content) but requirement
8's actual guarantee held: nothing outside that one document's own row was
touched.

### Stage 7 real-fixture verification

Run against the actual pinned model (Bielik-4.5B-v3.0-Instruct.Q8_0, via a
locally-started `llama-server`), not just the `fake` backend, per CLAUDE.md's
"verify against real fixtures" rule — this dev machine is x86_64 Linux,
CPU-only, 4 threads, a different machine entirely from the M1 target, so
timing numbers here are not representative of the eval machine, only
correctness is. The schema-embedding bug above was found and fixed this
way; after the fix, a real run through the actual CLI path
(`inventory.build_inventory` → `orchestrate.run` → real `llama-server` →
`validate_extraction` → db write, `--workers 1`, `--ctx-size 2048`,
`--parallel 1`) completed two real corpus documents end-to-end with
plausible, schema-valid output — including a genuinely correct answer on
one of the corpus's adversarial currency-precedence cases
(`Offer_facade_renovation.eml`, a Swedish counterparty: the model correctly
returned `currency: "SEK"`, not `EUR`, which is exactly `docs/DATA_SPEC.md`
§3.2's "no euro-zone assumption from an EU VAT prefix" trap case, resolved
correctly from prose instructions alone with no schema decoding forced
value). Per-request latency on this dev box (~140-280s for 150-300 output
tokens, CPU-only, 4 threads, no GPU offload) is far above what the M1 target
should see per `docs/PROJECT_NOTES.md` §5's estimates (~15s/doc for a 4.5B
model) and is not a Stage 7 defect — it did surface that the default 60s
per-request client timeout (Stage 5's `LlamaServerClient`/`OllamaClient`
default, already flagged as unvalidated below) is too tight for *this*
specific slow environment; not changed here since raising it without a
measurement on real M1 hardware would just be a different unvalidated guess.

### Stage 8 real-fixture verification

Run against the real `data/corpus` (not a synthetic one-file fixture) with
the `fake` backend — a real run's `report` output balances
(`input_files=46 = duplicate_files=10 + unique_documents=36`;
`unique_documents=36 = processed_ok=0 + quarantined=36 + not_started=0`,
every document forced to quarantine since the fake backend's default
response is `{}`, invalid against the schema) — and `eval` against the real
`data/expected.jsonl` produced exactly `not_found_in_db=2` (the two
git-ignored `huge_log_*` files, absent because `scripts/make_huge_file.py`
wasn't run in this checkout — confirmed independently via `data/MANIFEST.md`,
not assumed) and `dedup_mismatches=0`. Per-field accuracy numbers were
non-zero and *consistent with* every real document scoring wrong (forced
quarantine) while corrupt/other documents whose expected value is
legitimately `null` for a given field (not just `doc_type`, e.g.
correspondence with no `due_date`) still matched on that field — the
expected shape for a run that never actually extracted anything, not a
sign of a scoring bug. `summary`'s loose score was `0.0%`, correctly, since
the fake backend never produces a non-empty summary.

### Test suite (Stage 9)

docs/ZADANIE.md requirement 10's list ("at least: resume after
interruption, budget stop, report balance, an unprocessable document")
plus docs/PROJECT_NOTES.md §8 Stage 9's fuller version (adds injection,
`--workers 1` vs `16`, memory on a huge file) — all against the `fake`
backend, never a real model/network/API key. Most items already had
coverage from the stage that introduced the behaviour (budget stop and
report-balance in `test_orchestrate.py`/`test_report.py`, unprocessable
documents across `test_inventory.py`'s corrupt fixtures and
`test_orchestrate.py`'s repair-exhausted case, injection in
`test_orchestrate.py`). Stage 9 adds the pieces that genuinely needed new
machinery:

**A real `SIGKILL` against a real, spawned `extractor run` process**
(`tests/test_resume_subprocess.py`) — every earlier resumability test
(Stage 7) runs orchestration in-process against a `ThreadPoolExecutor`,
which proves the *logic* (`in_progress` reset, no double-claim) but never
crosses a real process boundary or an un-catchable signal. This test does:
spawns `python -m extractor.cli run ...` as a real subprocess, polls the
db from *outside* that process until at least one document is durably
`quarantined`, `SIGKILL`s it, resumes with the identical command against
the same db, then asserts two separate things — the final record set
matches a fresh clean run over the same input (requirement 4's literal
wording), **and** the documents that were already terminal before the
kill gained zero additional `token_ledger` rows after resume. The second
check is load-bearing on its own: with a fully deterministic `fake`
backend, comparing only final field values could never catch a bug that
silently reprocessed an already-completed document, since reprocessing a
deterministic backend reproduces the identical answer. Needed a new,
test-only `[backend.fake] delay_s` config knob (`FakeLLMClient(delay=...)`,
wired through `extractor.llm.build_client`) — with no artificial latency
the fake backend has no real I/O at all, and a handful of documents can
finish before an external poll loop (running in a different process)
observes even the first completion, making "kill strictly mid-run" wholly
unreliable to hit.

**`--workers 16`, not just `4`** (`test_workers_1_and_workers_16_produce_
identical_final_documents`) — Stage 7's own worker-independence test used
4 workers over 8 documents; requirement 5 states the bar as literally "N=1
i N=16", so Stage 9 adds a 16-workers/20-documents variant (enough
documents that 16 threads are actually all doing something, not mostly
idle) rather than treating the smaller case as sufficient evidence.

**Memory on a huge file** (`tests/test_memory.py`) — generates a fresh
300MB synthetic `.txt` into `tmp_path` (deliberately *not* depending on
`data/corpus`'s real, git-ignored `huge_log_*` fixture, since regenerating
it via `scripts/make_huge_file.py` also appends rows to the committed
`data/expected.jsonl` as a side effect — a test must never trigger that
implicitly) and asserts `resource.getrusage` RSS growth, for both this
process (`RUSAGE_SELF`, covers `inventory.py`'s streamed hashing) and its
extraction subprocess (`RUSAGE_CHILDREN`, covers `textextract.py`'s
`MAX_TEXT_CHARS`-capped read), stays far below the file's own size — a
generous 150MB canary threshold, empirically ~65MB in practice. This is
**not** the requirement-9 2GB acceptance check itself: that number is a
property of the real M1 machine and macOS's `ru_maxrss` is in *bytes*
where Linux's is in *KB* (already flagged above), so it's checked for real
at Stage 10 via `/usr/bin/time -l`, not asserted portably in CI. What this
test guards is the structural property that makes the 2GB bound plausible
at all — the file is genuinely streamed/capped, never loaded whole — and
it would clearly fail (blow past the canary by 2×+) if that regressed to
`Path.read_bytes()` on the whole file.

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
- ~~Windowing's char-budget proxy is still the default~~ — resolved by
  Stage 7: `orchestrate.default_token_counter()` wires the offline
  `tokenizer.py` counter through as the real default, sized per run by
  `_context_window_budget()` (see the Stage 7 decisions above). The proxy
  remains reachable (`count_tokens=None` in tests, or a fork with no
  `assets/tokenizer.json`) but is no longer what a real `run` uses.
- Retry count, backoff base, and circuit-breaker threshold are hardcoded
  `ResilientLLMClient` defaults (3 retries, 1s base, 5 consecutive
  failures), not yet exposed through `config/default.toml`. **Per-request
  timeout is now configurable** (`[backend.llama_server]`/`[backend.ollama]`
  `timeout_s`, `extractor.llm.build_client`), added after this gap caused a
  real `backend_unavailable` run on a slow CPU-only dev box (per-request
  latency there is ~140-280s against the 60s hardcoded default, well past
  the circuit breaker's failure threshold) — but `config/default.toml`
  itself still doesn't set it, so the M1 target's default behaviour is
  unchanged, and the *right* default for real M1 hardware is still an open
  question, not resolved by making it configurable. The real ~107s Ollama
  cold-start measured at Stage 5c still suggests the eventual default needs
  to be well above a naive guess, or a separate, longer "model loading"
  timeout distinct from the steady-state per-request one.
- ~~Neither `llama-server`'s nor Ollama's process/model lifecycle... is
  managed by this tool yet~~ — resolved for `llama_server` by
  `extractor.llm.lifecycle` (see the decision above): `extractor.cli.run`
  self-heals (starts it if `/health` doesn't answer) and
  `scripts/smoke_test_backend.py` starts it at install time. **Still true
  for Ollama** — its client only ever talks to a server already running,
  and nothing starts, health-checks, or stops one; a reviewer who switches
  `config/default.toml` to `ollama` needs to run it themselves. Not
  addressed together with `llama_server` for the same reason
  `fetch_runtime.py` never fetches Ollama's model either (below): this
  session's actual `backend_unavailable` failure and manual-start workflow
  were both against `llama_server`, and Ollama's own lifecycle (`ollama
  serve`, `ollama pull`) is already a single external command a reviewer
  runs themselves, unlike `llama-server`'s multi-flag,
  `--ctx-size`-must-scale-with-`--parallel` startup this module exists to
  get right.
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
- ~~`validate_extraction()` is not wired into anything yet~~ — resolved by
  Stage 7: `orchestrate._attempt()` calls it on every response, maps
  `needs_repair=True` to one `build_repair_prompt()` call, and quarantines
  as `llm_invalid_output` if that repair attempt is also unusable.
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
- `_context_window_budget()`'s `2 * max_output_tokens` term (Stage 7) is a
  conservative heuristic sized for the repair turn's worst case (a
  near-`max_output_tokens`-length previous response, echoed back, plus a
  new completion of the same cap), not an exact accounting of what a repair
  prompt will actually cost. It errs toward leaving *less* room for document
  context than strictly necessary on the common (no-repair) path, never
  toward risking a context overflow — the direction that matters, since the
  overflow this stage found and fixed silently dropped every request rather
  than degrading gracefully.
- `llama-server`'s shared-KV-cache-across-slots behaviour (`kv_unified`,
  the decision above) means the practical, safe upper bound on `--workers`
  against a real `llama_server` backend is capped by how the server process
  was started. When `extractor.llm.lifecycle` is what starts it (the normal
  path now — see the decision above), `--parallel` is always set to the
  same `config.workers` used for `--ctx-size`'s multiplication, so the two
  can't drift apart. `orchestrate.py` itself still has no way to know or
  check this, though: a server started by hand before `run` (left alone by
  `ensure_running()`'s health-check-first behaviour) can still have any
  `--parallel` value a human gave it, and neither `ensure_running()` nor
  `orchestrate.py` inspects `/slots` to confirm the two match. `--workers
  16` (requirement 5's stability bar) was exercised only against the
  `fake` backend, which has no such ceiling.
- The circuit breaker's "last straw" attribution gap (a document whose own
  retries exhaust just before the *next* document's failure would have
  tripped the breaker gets individually quarantined instead of counted as
  part of the same outage) is stated above as a deliberate non-fix, not
  resolved.
- `report`'s `wall_time_s` for an interrupted run (no `ended_at`) is a
  lower-bound approximation (latest `token_ledger` timestamp minus
  `started_at`), not the true kill time, which is unrecoverable once the
  process is gone. Every other run row's contribution is exact.
- `eval`'s `summary` loose metric has no language-match check (would need a
  language-detection dependency for one sub-component of a metric that is
  already advisory, not the headline score) and its keyword logic is a
  short hand-picked stoplist plus longest-remaining-word heuristic, not
  NER — a counterparty name that is entirely generic words falls back to
  its single longest word regardless of whether that word would actually
  appear in a good summary. Its one-sentence shape check
  (`.`/`!`/`?` + whitespace + capital letter = a new sentence, fixed after
  a real-run bug — see "Report and eval" above) is still a heuristic, not
  a real sentence-boundary detector: an abbreviation followed by a
  capitalised proper noun (rare in this corpus, not ruled out in general)
  would still be misread as two sentences.
- `nip_checksum_valid()` (Stage 6) is still unwired into anything —
  `report` deliberately does not surface a "checksum failures" count (see
  "Report and eval" above: it would mostly flag foreign counterparties'
  non-Polish tax IDs as false failures, since the check only recognises
  10-digit Polish NIP shape).
- `tests/test_resume_subprocess.py`'s real-`SIGKILL` test is inherently
  timing-based (poll-from-outside-the-process, `delay_s`-paced): reliable
  and repeatable on this dev machine (run several times back to back with
  no failures) but, like any test that waits on wall-clock behaviour of a
  spawned process, a much slower or heavily loaded CI runner could in
  principle race the poll loop against the process finishing all
  documents before the first observed completion — guarded against by an
  explicit assertion (`len(pre_kill_ids) < n_docs`) that fails loudly
  rather than silently passing a degenerate run, but not eliminated.
- `tests/test_memory.py`'s RSS-growth threshold (150MB canary for a 300MB
  file) is a heuristic regression guard, not the requirement-9 acceptance
  measurement itself — see "Test suite" above for why that has to happen
  on the real M1 machine instead.
- `eval`'s per-field accuracy denominator is every document in
  `expected.jsonl`, always — a document `run` never reached (still
  `pending` under `--limit`/`--budget`) scores as wrong on every non-null
  field rather than being excluded, which is a deliberate choice (see
  "Report and eval" above) but does mean the accuracy number conflates "the
  model got it wrong" with "the model never saw it"; `report`'s own
  `not_started` count is the way to tell the two apart for a given db.

## Throughput bottleneck (requirement 5)

One inference server, one GPU. Prompt processing is compute-bound, so
parallel server slots barely help beyond a small number — near-sequential
throughput should be assumed. The dominant lever on latency is context
size sent to the model, not model size, which is why Stage 4 windows long
documents instead of sending full text. `--workers` controls concurrency
for everything *except* the model call; it doesn't increase model
concurrency, a separate backend-side slot count.

The request/response plumbing is now confirmed end-to-end through
orchestration itself, not just the client layer: a real `extractor run`
against the real pinned model (see "Stage 7 real-fixture verification"
above) produced correct, schema-valid, semantically-correct output —
including a currency-precedence trap case resolved correctly from prose
alone. Wall-clock throughput against the M1 budget (20 min / ~40 docs) is
still *not* confirmed — this dev environment is x86_64 Linux, CPU-only, 4
threads, a different machine entirely, and per-request latency observed
here (~140-280s for 150-300 output tokens) is far outside what the M1
target should see; the M1's actual numbers need `PROJECT_NOTES.md` §5's
estimation approach or real hardware. `--workers` beyond the server's real
`--parallel` slot count queues, as designed — but the server's `--ctx-size`
also has to scale with `--parallel` (`kv_unified` shares one KV-cache budget
across all slots), a real, found-not-designed-for constraint documented
above; `extractor.llm.lifecycle` (the "Key decisions" entry above) now
handles this whenever it's the one starting the server, but a
manually-started one is still on the human who started it to size
correctly.

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

Stage 7's orchestration loop (one sqlite3 connection per worker thread,
each write its own short `BEGIN IMMEDIATE ... COMMIT`, serialised by WAL +
`busy_timeout`) is fine at 40 documents and `--workers` up to 16 — writes
are short and infrequent relative to the model call that dominates wall
time. At 100×, with many more workers needed to keep a batching-capable
inference backend fed, single-writer SQLite becomes the bottleneck *before*
the inference server does: every worker's claim/reserve/write still
serialises through one file, and `busy_timeout`-based backoff turns into
real queuing delay rather than the negligible cost it is today. This is the
same "Postgres instead of SQLite" change already named above, motivated
concretely by Stage 7's own write pattern rather than abstractly.
