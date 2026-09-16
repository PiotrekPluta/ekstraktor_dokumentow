# Here is Claude's plan for stage 7:

 **Stage 7: orchestration, resume, and budget**

 **Context**

 docs/PROJECT_NOTES.md §8 calls this "the core of the task." Everything
 built so far (Stage 2 inventory/dedup, Stage 3 extraction, Stage 4
 windowing, Stage 5 LLM client, Stage 6 validation) is a library of
 building blocks; nothing yet calls them in sequence. extractor.cli.run
 is still the Stage 0 stub (src/extractor/cli.py:44-58) that just prints
 "not implemented". Stage 7 turns it into the real command: claim pending
 documents in deterministic order, build a prompt, call the model, validate
 the response, write the result — and do all of that in a way that survives
 SIGKILL at any point (requirement 4), scales from --workers 1 to 16
 without changing the result set (requirement 5), never overspends
 --budget (requirement 6), and never lets a document's own content touch
 any row but its own (requirement 8).

 I re-read docs/ZADANIE.md's requirements 3-9 verbatim and every relevant
 existing module (config.py, db.py, inventory.py, windowing.py,
 llm/__init__.py, llm/resilience.py, llm/llama_server.py,
 validation.py) to ground this plan in what already exists rather than
 re-describing it. Findings and decisions below.

 Decisions that need to be settled before writing code

 1. A real gap found while reading the code: extracted text is never
 persisted. inventory.py's _extract_worker computes
 normalize_text(result.text) only to hash it into the document's identity,
 then discards result.text entirely — documents has no text column.
 Stage 7 needs that text back (for windowing and for Stage 6's
 occurs_in_source), so it would otherwise have to re-run extraction a
 second time per document. Decision: add documents.source_text (nullable
 TEXT), populated once by inventory.py at Stage 2/3 time — a small,
 justified extension of already-committed code, not a new stage. Stores the
 original result.text (pre-normalize_text), not the casefolded/
 divider-stripped identity version: windowing.clean_for_context wants
 case/structure preserved (its own docstring says so), and occurs_in_source
 already calls normalize_text internally, so handing it non-normalized text
 is correct either way. Quarantined-at-inventory documents keep
 source_text IS NULL (nothing to store, same as identity_kind='bytes'
 today). This avoids double extraction cost — real, since the "100x scale"
 section of ARCHITECTURE.md already flags per-file extraction overhead as
 a bottleneck; doing it twice would double it for nothing.

 2. --limit/--budget are cumulative across the whole db's history, not
 per invocation. docs/PROJECT_NOTES.md §3's open question 2, never
 answered by the recruiter (they only answer ambiguity questions, not
 "is X enough"). Requirement 4 is unconditional: "after any number of
 interruptions and resumes, the record set in the db is identical to an
 uninterrupted run." If --limit 5 reset per invocation, three interrupted
 runs would process up to 15 documents, not 5 — violating that guarantee
 outright. So: --limit N means "at most N documents get an LLM attempt,
 ever, in this db's lifetime" (tracked by counting done +
 quarantined(llm_invalid_output) rows — documents quarantined during
 inventory, e.g. corrupt_file, never reached the LLM step and don't
 count against it). --budget N is enforced the same way, and gets it for
 free: token_ledger already accumulates reserved_tokens across every
 run_id, so "cumulative spend so far" is just SUM(reserved_tokens) over
 the whole table, not scoped to the current run. db.py's own schema
 docstring already anticipated this ("this schema supports either" — Stage 7
 is what actually picks one).

 3. Requirement 9's "2GB, the process" is read as the whole process tree
 (main + any subprocess workers), the conservative reading, per
 docs/PROJECT_NOTES.md §3's open question 3 (also unanswered). Stage 7
 adds no runtime memory-limiting code — the brief tests this empirically
 at Stage 10 via /usr/bin/time -l, not via an in-tool enforcer — but the
 design has to not break the boundedness Stage 2/3 already built:
 ThreadPoolExecutor(max_workers=config.workers) bounds how many documents'
 source_text/windowed-context are ever in memory at once to --workers,
 never the whole ~40-file (or few-thousand-file) corpus. This gets stated
 in ARCHITECTURE.md, not enforced by new code.

 4. Concurrency model: threads, not processes, for the orchestration
 loop. The per-document work here is I/O-bound (an HTTP call to the model
 server dominates), so ThreadPoolExecutor is the right primitive — no
 GIL contention, no multiprocessing-pickling complexity like Stage 3's
 extraction subprocesses (which are CPU/C-library-bound and need real
 process isolation for a different reason: killing a hung pypdfium2 call).
 The target document-id list (pending, sorted by id, cut to the
 remaining --limit) is computed once, up front, from a single query —
 so the set of documents attempted is fixed before any worker starts, and
 is identical regardless of --workers. Workers pull from a
 queue.Queue seeded with that fixed list; each worker opens its own
 sqlite3 connection (WAL mode already means one writer at a time; each
 write happens in its own short BEGIN IMMEDIATE ... COMMIT, so sqlite3's
 own busy-timeout serialises conflicting writers rather than erroring).

 5. Found while reading llm/resilience.py: CircuitBreaker/
 ResilientLLMClient have no lock. _consecutive_failures/_open are
 plain, unguarded attributes. One ResilientLLMClient instance has to be
 shared across all worker threads (the whole point of the breaker is a
 global "backend is down" signal — one per worker would mean a truly-down
 backend takes workers × as long to detect), so concurrent complete()
 calls from --workers 16 are a real, currently-unguarded race on that
 shared state. Decision: add a threading.Lock around the breaker's
 read-modify-write in ResilientLLMClient.complete() (retry/backoff
 sleeps stay outside the lock — only state transitions need it). Small,
 targeted change to already-committed Stage 5 code, directly required by
 Stage 7 being the first stage to call .complete() concurrently.

 6. Reusing llm_invalid_output for "no usable response after retries",
 not inventing a 7th quarantine reason. The closed enum
 (docs/PROJECT_NOTES.md §8 Stage 3) has six values; extending it needs a
 schema+CHECK-constraint change I don't think is warranted for what's really
 the same outcome from the document's point of view: "this document did not
 get a valid extraction." Two paths lead there — the model responded but the
 JSON was unusable even after one repair attempt (Stage 6's own doorway into
 this reason), or ResilientLLMClient.complete() raised after exhausting
 retries without tripping the breaker (a document-scoped failure — the
 breaker didn't open, so this isn't a whole-run backend_unavailable
 event, just this one document's bad luck). Both quarantine with
 llm_invalid_output; documented explicitly in ARCHITECTURE.md as a
 deliberate reuse, not an oversight.

 7. Token reservation stays conservative across resumes, per
 docs/PROJECT_NOTES.md §8's own spec. A reservation row is inserted
 before the call; a process killed between response and write leaves that
 reservation's tokens_in/tokens_out/completed_at all NULL — and it is
 never "released": the cumulative budget check (SUM(reserved_tokens))
 counts it forever, matching "the budget cannot be exceeded" literally
 rather than trying to detect and roll back an abandoned attempt. A resumed
 document gets a fresh reservation row for its new attempt
 (token_ledger.attempt increments, UNIQUE(run_id, document_id, attempt)
 already supports this with no schema change).

 8. New run-scoped config: [run] max_output_tokens (proposed
 default 300 — generous for one compact JSON object with a one-sentence
 summary, an order of magnitude below the ~1500-2000 token/doc input
 budget docs/PROJECT_NOTES.md §4 sets). This is LLMRequest.max_tokens
 and the fixed worst-case component of every reservation.

 Files

 src/extractor/db.py: add source_text TEXT to CREATE TABLE documents (nullable, no CHECK — absent exactly when identity_kind='bytes'
 today, not worth a constraint for that correlation). No other schema change
 — documents.backend/model/run_id and token_ledger already have
 everything else Stage 7 needs to write.

 src/extractor/inventory.py: _extract_worker also puts
 result.text (original, pre-normalize_text) on the queue payload
 alongside the normalized identity text; _process_entry/ProcessedEntry
 carry it through; _insert_document writes it into the new column.

 src/extractor/llm/resilience.py: add a threading.Lock guarding
 CircuitBreaker's state transitions and the retry-loop's
 check-then-act sequence in ResilientLLMClient.complete().

 src/extractor/prompt.py — new. build_prompt(context: str) -> str
 and build_repair_prompt(context: str, previous_response: str, error: str) -> str. ChatML-formatted (<|im_start|>system ... <|im_end|> etc. —
 Bielik/Qwen2.5-family template, matching ARCHITECTURE.md's existing "Bielik
 uses ChatML" note). System instructions state the task, the eight fields,
 extractor.schema.ExtractedFields.model_json_schema() inline, and the two
 pieces of document-level reasoning Stage 6 explicitly does not do itself
 (docs/PROJECT_NOTES.md §2, validation.py's own scope-boundary
 docstring): counterparty is the seller, never the buyer, even when the
 buyer is more prominent; and the full currency precedence chain
 (docs/DATA_SPEC.md §3.2's six levels) as instructions the model applies
 using the seller-country context in the windowed text. The repair variant
 replays the conversation as a second user turn quoting the previous invalid
 response and the validation error, asking for a corrected JSON object only
 — the /completion endpoint is stateless, so this has to be re-sent in
 full each time, not appended to server-side state.

 src/extractor/orchestrate.py — new. The run() entry point
 cli.py will call:

 - Reset any in_progress documents to pending (requirement 4 — a row in
   that state means a worker claimed it but we can't tell if the model call
   completed).
 - Insert the runs row (started_at, requested workers/limit/
   budget, resolved backend/model tag from config).
 - Compute the fixed target-id list per decision 2/4 above; if empty, finish
   immediately with stop_reason='completed'.
 - ThreadPoolExecutor(max_workers=config.workers) draining a
   queue.Queue of that list. A shared, lock-guarded accountant tracks
   cumulative reserved tokens and the "should we stop" flag (limit/budget/
   breaker-open), checked before a worker claims its next id — not just at
   the end of a batch — so a stop condition mid-run doesn't let extra
   documents start.
 - Per document: claim (UPDATE ... WHERE status='pending', own
   transaction) → fetch source_text → windowing.build_context (wiring
   extractor.tokenizer.count_tokens through as the real counter, closing
   the gap ARCHITECTURE.md already flags: "nothing wires either counter
   through by default yet") → prompt.build_prompt → reserve tokens (own
   transaction) → client.complete() → validation.validate_extraction() →
   on needs_repair, one prompt.build_repair_prompt + second
   client.complete() + re-validate → write final result (documents
   UPDATE + ledger UPDATE, one transaction, single parameterised statement
   set scoped to this document's own id — requirement 8).
 - Catches: LLMBackendUnavailable → release the document to pending,
   set the shared stop flag with stop_reason='backend_unavailable'; other
   propagated LLMError → quarantine just this document
   (llm_invalid_output, decision 6), keep going.
 - On completion (queue drained or a stop condition tripped): write
   runs.ended_at/stop_reason, close out.

 src/extractor/cli.py: run calls orchestrate.run(...) instead of
 _not_implemented, opening the db via extractor.db.connect and building
 the client via extractor.llm.build_client.

 Tests

 All using the fake backend (requirement 10) — extended in one small,
 additive way: FakeLLMClient gains an optional responses: list[LLMResponse] sequencing mode (returns them in order, then repeats
 the last), needed to test the repair-attempt path (invalid JSON, then
 valid) without inventing a second test double.

 tests/test_orchestrate.py:

 - A full run against a small in-memory/temp db with a few pending
   documents completes all of them with fake's canned fields written
   correctly; runs.stop_reason='completed'.
 - Resume: a document manually set to in_progress before run() is
   called gets reset to pending and completed — not lost, not
   double-processed.
 - No repeated model calls: a document already done before run() is
   called is absent from fake.calls afterward (requirement 4, literally).
 - --limit is cumulative across two sequential run() calls sharing one
   db: second call processes only limit - already_done, not limit
   again.
 - --budget: a reservation that would exceed the budget stops the run
   before calling the model for that document; stop_reason='budget_exhausted';
   the document stays pending.
 - --workers 1 vs --workers 4 against the same fixed pending set (fake
   backend, no real network latency) produce an identical final
   documents table (status + fields), proving the result set doesn't
   depend on worker count (requirement 5).
 - Repair path: FakeLLMClient(responses=[invalid_json, valid_json])
   completes successfully using the second response; exactly 2 calls
   recorded for that document.
 - Repair exhausted: FakeLLMClient(responses=[invalid, invalid]) (or
   always_fail shaped invalid JSON) quarantines with
   llm_invalid_output, run continues to other documents (not aborted).
 - always_fail=True fake trips the circuit breaker: run stops with
   stop_reason='backend_unavailable'; unattempted documents stay
   pending — no document lost (requirement 5's "must not lose a document
   or hang forever").
 - Concurrency: --workers 8+ against an always_fail fake completes
   without a crash/race exception and the breaker's open state is
   consistent (exercises decision 5's lock).
 - Integrity: a fake response engineered to look like it targets a
   different document id in its content only ever changes the row for the
   document actually being processed — assert every other row is byte-for-
   byte unchanged after the run (requirement 8, structural check mirroring
   docs/DATA_SPEC.md §5's own test requirement).
 - Report-balance precondition (Stage 8 will build report itself, but the
   underlying data must already satisfy it): after a run mixing done/
   quarantined/pending, SELECT COUNT(*) FROM documents breakdown
   satisfies unique_documents = processed_ok + quarantined + not_started
   by direct SQL query.

 tests/test_prompt.py: build_prompt includes the JSON schema, the
 seller-not-buyer instruction, and the currency-precedence instructions;
 build_repair_prompt includes the previous invalid response and the error
 message.

 tests/test_llm_resilience.py: add a concurrent-complete() test (several
 threads hammering an always_fail fake through one shared
 ResilientLLMClient) asserting the breaker opens exactly once and no
 exception escapes from a race, not just from the sequential paths already
 covered.

 Extend tests/test_inventory.py for the new source_text column: a
 successfully-extracted document has non-null source_text matching (pre-
 normalization) extracted content; a quarantined-at-inventory document has
 source_text IS NULL.

 Verification

 1. uv run pytest tests/test_orchestrate.py tests/test_prompt.py -v, then
    the full suite (uv run pytest -q) and uv run ruff check / ruff format --check.
 2. Real-fixture check (CLAUDE.md's "verify against real fixtures" rule):
    run extractor run --input data/corpus --db /tmp/stage7.sqlite --backend llama_server --limit 3 against the actual running llama-server +
    pinned Bielik model, confirm real documents complete with
    plausible, schema-valid field values — not just that the fake-backed
    unit tests pass. Mirrors how Stage 5b/5c were verified against a real
    server before being called done.
 3. Manual resume check: run with --limit 5, kill -9 the process
    mid-run, rerun the identical command, confirm the record set matches
    an uninterrupted --limit 5 run and no already-done document
    triggers a new model call (visible in llama-server's own request log).