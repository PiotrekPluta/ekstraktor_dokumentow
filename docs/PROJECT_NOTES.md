# Project notes — document extractor (recruitment task)

Working reference distilled from the planning conversation. Written in English
for consistency with `DATA_SPEC.md`; document content and `summary` output
remain Polish-first per the brief.

Companion file: **`DATA_SPEC.md`** — the full specification for the synthetic
corpus generator. This document does not repeat it.

---

## 1. What the task actually tests

Ten acceptance criteria, of which only a minority concern extraction quality.
The weight sits on engineering around the model:

- resumability after `SIGKILL` with no repeated model calls,
- a hard token budget that must never be exceeded,
- deduplication across encodings, formats and filenames,
- a report that balances arithmetically,
- integrity against untrusted document content,
- tests that pass with no network, no API key and no inference server.

Two lines from the brief set the bar: *"We do not expect perfection. We expect
you to know what your solution does not do."* and *"we check whether `eval`
results on your data say the same thing as on ours."*

Practical consequence: if time runs short, finish resumability, budget, report
balance, integrity and quarantine first. Extraction accuracy last.

---

## 2. Decisions locked so far

| Decision | Value | Where it lives |
|---|---|---|
| Counterparty | **Seller / offeror / contractor**, never the buyer. Buyer is assumed to be the client. | `DATA_SPEC.md` R0.6 |
| Currency precedence | explicit ISO code → unambiguous symbol → ambiguous symbol + seller country → seller postal address country → seller VAT ID prefix → `null` | `DATA_SPEC.md` §3.2 |
| VAT prefix as country signal | **Yes**, but only when no seller postal address exists at all. Address always beats prefix. Buyer's VAT ID is never a signal. | `DATA_SPEC.md` §3.2 |
| VAT prefix → currency | Explicit lookup table, never a euro-zone assumption (`SE`→`SEK`, `CZ`→`CZK`, `EL`=Greece, `XI`=N. Ireland) | `DATA_SPEC.md` §3.2 |
| Ground truth | Generated first; documents rendered from it. Never hand-labelled. | `DATA_SPEC.md` R0.1 |
| Generator isolation | Shares no code with the extractor | `DATA_SPEC.md` R0.2 |
| Default model | Bielik-4.5B-v3.0-Instruct, context capped ~1500–2000 tokens/doc | §4 below |
| OS / toolchain | Ubuntu 24.04 LTS, Python 3.12 via `uv` | §6 below |

---

## 3. Open questions

### For the recruiter 

They answer questions about ambiguities, not "is X enough" questions.

1. **`.eml` attachments** — is an attached invoice a separate document with its
   own record, or part of the email? Affects `input_files` /
   `unique_documents` counts and the `files` list. Edge case: the same invoice
   present both as a standalone PDF and as an attachment.
2. **`--budget` / `--limit` on resume** — the wording says "in a given run", but
   the requirement that the record set match an uninterrupted run only holds if
   both are cumulative across the whole job. Three interrupted runs at
   `--budget 10000` would otherwise process more than one clean run.
3. **2 GB memory limit** — main process only, or including child worker
   processes? (Assuming the inference server is excluded.)
4. **`gross_amount` for contracts and offers** — the contract value?
5. **`summary` scoring in `eval`** — exact match is meaningless; what do they
   expect?

### Still to decide internally

- Failed mod-11 NIP checksum: emit as found, or emit `null`? Either is
  defensible; the answer must be stated in `ARCHITECTURE.md`.
- `$` on an English invoice with no other signal: `USD` or `null`?
- Two byte-identical corrupt files: one `expected.jsonl` line or two?
  (Recommendation: one — dedup runs before parsing.)

---

## 4. Model and backend

### Constraint that drives everything

The evaluation machine is a base Apple M1, 16 GB unified memory. Full run of
~40 documents at `--workers 4` must finish in **20 minutes**; `--limit 5` in
**3 minutes**, including model load.

### Candidates

**Bielik** (SpeakLeash) — recommended. Apache 2.0 including commercial use.
Family: 1.5B and 4.5B v3.0 (Qwen 2.5 architecture, Polish-tuned tokenizer),
Minitron-7B v3.0, 11B v3.0 (GGUF and Ollama available, MLX 8-bit variant).
Version 3.1 with 50-language support was announced for June 2026 — verify on
Hugging Face before pinning.

**PLLuM** — workable but less convenient. Llama-PLLuM-8B-instruct at Q4_K_M is
~5 GB; PLLuM-12B Q4_K_M ~7.5 GB; 8x7B won't fit (~27 GB even quantised). The
`nc` variants are CC-BY-NC, so no commercial use — fine for a recruitment task,
not fine for a real client. Llama-based variants inherit the Llama licence.

**Also worth benchmarking:** small multilingual Qwen / Gemma models. Field
extraction is mostly reading and emitting JSON; Polish matters most for
`summary`. Settle it with your own `eval` across 2–3 candidates — that
comparison table is a strong `ARCHITECTURE.md` paragraph.

### Two things that matter more than model choice

1. **JSON-schema-constrained output.** Ollama takes a JSON Schema in `format`;
   `llama-server` takes `json_schema`. Without it, a small model will hand back
   broken JSON often enough to matter.
2. **The model is not the only source of truth.** Regex candidate extraction and
   validators for NIP, dates and amounts; send the model only the relevant
   windows of long documents.

### Backends (config-selectable, requirement 2 needs at least two)

- **`llama-server`** (llama.cpp) — primary. GGUF pulled from Hugging Face at a
  pinned repo commit, sha256 verified. That is the "digest".
- **Ollama** — secondary. Pinned tag; the tool verifies the manifest digest at
  startup and refuses to run on mismatch. *Trap: Ollama's default `num_ctx` is
  small and silently truncates longer input — set it explicitly.*
- **`fake`** — test backend, returns canned responses and counts calls.

---

## 5. Estimating M1 performance without a Mac

Token counts per document are hardware-independent, and the tool already records
`tokens_in` / `tokens_out` for the report. So measure locally, then convert.

From the llama.cpp benchmark discussion, a base M1 (68 GB/s, 7–8 GPU cores) on a
7B Q8_0 model: **prompt ≈ 108 tok/s, generation ≈ 14 tok/s**.

```
seconds_per_doc ≈ tokens_in / 108 + tokens_out / 14      # 7B; scale by params
```

Worked examples:

- 7B, 3000 in / 150 out → ~39 s/doc → 40 docs ≈ **26 min** (over budget), and
  `--limit 5` ≈ 3.3 min (also over).
- 4.5B, 1500 in / 120 out → ~15 s/doc → 40 docs ≈ **10 min** (comfortable).

**The dominant lever is context size, not model size.** Hence the windowing in
stage 4. On M1 the parallel server slots barely help, because prompt processing
is compute-bound — assume near-sequential throughput. This paragraph belongs in
`ARCHITECTURE.md` more or less verbatim.

**Memory:** rule of thumb is weights ≤ ~60% of unified memory, leaving room for
KV cache and the OS. At 16 GB that means ~9–10 GB for weights plus KV cache.
Bielik 4.5B at Q8 or 7B at Q4 fit comfortably; 11B at Q4 with four slots is
tight.

**Action item:** write `scripts/estimate_m1.py` that reads the DB after a run
and projects M1 wall time from speeds in the config. Decide with numbers, and
say in `ARCHITECTURE.md` where the numbers came from.

### Verifying on real hardware

- **AWS `mac2.metal`** — physical Mac mini M1, 8-core CPU, 8-core GPU, 16 GB.
  $0.65/hour but **24-hour minimum** dedicated host allocation, so ~$15–16 per
  session. New accounts may need a quota increase for dedicated hosts — request
  it days ahead. Go in with a checklist: clean install from README, full run at
  `--workers 4`, `--limit 5`, `/usr/bin/time -l` for peak memory,
  `--workers 16`, tests with networking off.
- **A borrowed Mac** — an hour on any Apple Silicon machine. If it's M1 Pro or
  newer, results beat the evaluation machine, so leave headroom.
- **GitHub Actions `macos-14`** — correctness only, never performance. Public
  repos get a 3-core M1 VM with 7 GB RAM, and GitHub's arm64 docs state Metal
  Performance Shaders don't work under Apple's virtualisation. Use it for the
  `fake`-backend test suite on every commit, plus an end-to-end install smoke
  test (`setup.sh`, tiny model, `llama-server -ngl 0`, `run --limit 1`).

---

## 6. Development environment (Ubuntu 24.04)

### Why not plain `venv`

Ubuntu 24.04 marks its Python as externally managed (PEP 668), so system-wide
`pip install` refuses anyway. But the real reason is the platform gap: you
develop on Linux x86_64, they evaluate on macOS arm64.

`uv` solves three things `venv` + `requirements.txt` does not:

1. **Manages the interpreter.** Downloads its own Python, independent of apt and
   of whatever is on the reviewer's Mac. `.python-version` containing `3.12`
   makes both sides identical.
2. **Universal lockfile.** `uv.lock` captures packages across OS, architecture
   and Python version. `uv pip compile`, like pip-tools, produces a
   platform-specific file that may not be portable — a guaranteed failure given
   the Linux → macOS gap.
3. **`uv sync --locked`** reproduces exactly or fails loudly.

Narrow the lock to the platforms that exist:

```toml
[tool.uv]
environments = [
    "sys_platform == 'linux'",
    "sys_platform == 'darwin' and platform_machine == 'arm64'",
]
```

### apt packages

```
sudo apt install build-essential cmake pkg-config git curl \
                 libcurl4-openssl-dev zip unzip sqlite3 fonts-dejavu-core
```

`libcurl4-openssl-dev` for building llama.cpp; `sqlite3` for inspecting the DB;
**`fonts-dejavu-core` for Polish glyphs in generated PDFs** — default PDF fonts
lack `ą ę ł ś ż`, which would silently hole the encoding test corpus.

Then `curl -LsSf https://astral.sh/uv/install.sh | sh`.

Do **not** apt-install `python3-pip` or `python3-venv`.

### Dependency selection rule

**Every dependency must ship a prebuilt wheel for macOS arm64 / CPython 3.12.**
Anything requiring a source build means the reviewer needs Xcode Command Line
Tools, and installation stops being one command.

Safe picks: `pypdfium2` or `pdfminer.six` (PDF — avoid PyMuPDF, AGPL),
`python-docx` (pure Python), stdlib `email` (EML), `selectolax` or
`beautifulsoup4` + `html.parser` (HTML), `charset-normalizer` (encodings),
`pydantic` (Rust core, but macOS arm64 wheels are published).

Verify from Linux: `uv` resolves for other platforms via `--python-platform`
with `--python-version`. It also has a switch forbidding source builds — run
sync with it in CI to catch a wheel-less dependency immediately.

### Layout

```
pyproject.toml
uv.lock            # committed
.python-version    # "3.12"
setup.sh
Makefile           # for you, not for the README
src/extractor/
tests/
scripts/
config/default.toml
data/
docs/DATA_SPEC.md
```

```toml
requires-python = ">=3.12,<3.13"

[project.scripts]
extractor = "extractor.cli:app"
```

The entry point gives `uv run extractor run --input … --db …`, i.e. exactly one
run command. Keep `ruff` / `pytest` in a dependency group, but **do** install it
in `setup.sh` — requirement 10 has the reviewer run the tests.

### `setup.sh`

POSIX `sh`, not bash — macOS ships bash 3.2, so arrays and `${var,,}` break.
Put the logic in Python.

```sh
#!/usr/bin/env sh
set -eu
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --locked
uv run python scripts/fetch_runtime.py
```

`fetch_runtime.py`, all with checksum verification:

1. Fetch the GGUF from Hugging Face at a **pinned repo commit**, not a branch;
   verify sha256. This is the requirement-2 digest.
2. Fetch a prebuilt `llama-server` binary from a pinned llama.cpp release
   (`macos-arm64` or `ubuntu-x64`). Prebuilt beats building — no cmake or Xcode
   needed on the reviewer's machine.
3. Write actual checksums to `vendor/versions.lock`.

The tool compares the model digest against config at startup and refuses to run
on mismatch. Cheap, and directly anticipated by *"we evaluate on exactly what
you pinned"*.

### Fresh-clone rehearsal

```
git clone . /tmp/probny && cd /tmp/probny && ./setup.sh
```

Stronger: the same inside a clean `ubuntu:24.04` container, with no `apt
install` beyond what the README lists as prerequisites.

---

## 7. macOS pitfalls (unverifiable from Linux — code defensively)

| Area | Issue |
|---|---|
| Shell | bash 3.2; no `sha256sum` (use `shasum -a 256`); `sed -i` and `timeout` differ or are absent |
| Multiprocessing | macOS defaults to `spawn` — force `spawn` locally too, so serialisation bugs surface early. Children don't inherit state; each opens its own SQLite connection |
| Filenames | Case-insensitive by default; directory listings return **NFD**, not NFC. Normalise paths to NFC on DB write and in `eval`, or `expected.jsonl` joins fail silently |
| Memory measurement | `ru_maxrss` is **bytes** on macOS, **kilobytes** on Linux |
| Docker | Containers get no GPU on macOS — the inference server must run natively |
| Windows | If you ever touch Windows, use WSL2; `SIGKILL` doesn't exist natively, so the resume test can't be written honestly |

Always pass `encoding=` explicitly when opening files. Both systems default to
UTF-8, but in a project about encodings, relying on a default invites trouble.

`.gitignore`: `*.gguf`, `vendor/`, `data/corpus/huge_*`, `*.sqlite`. The brief
says not to submit the result DB or logs.

---

## 8. Implementation plan

**Stage 0 — skeleton.** Python 3.12, `uv`, `typer` CLI, stdlib `sqlite3`,
`httpx`. `setup.sh` does install + model fetch (network is only available then).

**Stage 1 — data generator + `expected.jsonl`.** First, because it doubles as
test fixtures, model-selection benchmark, and an evaluated artifact. See
`DATA_SPEC.md`.

**Stage 2 — inventory and deduplication.** Stream the directory or zip without
full extraction; guard against zip slip and zip bombs. Detect type by magic
bytes, not extension. Two levels: sha256 of bytes (1 MB chunks), then a hash of
normalised text (decode → Unicode NFC → collapse whitespace → strip HTML tags).
The text hash is the document identity. **Be careful with fuzzy dedup** —
invoices from one template are nearly identical yet distinct documents. Group by
file size before hashing to keep the thousands-of-duplicates case cheap.

**Stage 3 — text extraction.** One parser per format, each in a subprocess with
a timeout so a corrupt file can't hang the run. Read PDFs page by page with a
character cap. Encoding resolution order: BOM → declared charset (mail header /
HTML meta) → `charset-normalizer`. Remember CP1250 and ISO-8859-2 differ exactly
on `ą ś ź`.

Quarantine reasons are a closed enum: `corrupt_file`, `unsupported_format`,
`empty_text`, `no_text_layer`, `parse_timeout`, `llm_invalid_output`.

**Stage 4 — context windowing.** For long documents, assemble context from the
head, the tail, windows around keywords, and regex candidate hits. Keywords:
`NIP`, `brutto`, `do zapłaty`, `termin płatności`, `total`, `amount due`. This
satisfies "regardless of where in the document" without sending 300 pages. Count
tokens with the model's tokenizer before the call — offline via `tokenizer.json`
or the backend's `/tokenize`.

**Stage 5 — LLM client.** Shared interface, per-request timeout, exponential
backoff with a retry cap, plus a circuit breaker: if the backend is unreachable
beyond a threshold, the run ends with `stop_reason=backend_unavailable` and the
documents stay `pending`. Nothing is lost; a resume finishes them.

*Key detail:* concurrent requests to the model is a backend parameter (server
slot count), **not** `--workers`. At `--workers 16` against 2 slots, requests
queue, exceed timeouts, get retried, and burn budget.

**Stage 6 — validation and normalisation.** Pydantic model with a `doc_type`
enum. NIP mod-11 checksum (weights 6,5,7,2,3,4,5,6,7). Amounts as `Decimal`
quantised to 0.01. Currency from an ISO 4217 allowlist, `zł` → `PLN`.

Additionally verify each model-returned value actually occurs in the normalised
source text. This blocks both hallucination and an injected "use this other
company's NIP". On failure: one repair attempt, then `null` or quarantine.

**Stage 7 — orchestration, resume, budget.** The core of the task.

SQLite in WAL mode. Tables: `files` (path → document id), `documents` (status,
fields, quarantine reason), `runs`, `token_ledger`.

Statuses `pending` / `in_progress` / `done` / `quarantined`; results written in
one transaction; `in_progress` reset to `pending` on startup.

Processing order is deterministic (sorted by document id), so `--limit N` means
a fixed set of the first N documents, not "the first N to finish". Only this
makes the result set independent of worker count and interruptions.

For the budget, **commit a reservation before the call**: input tokens plus
`max_tokens` output. If the reservation doesn't fit, the run ends with
`stop_reason=budget_exhausted`. A process killed between response and result
write leaves a conservatively-counted reservation, so the budget cannot be
exceeded. Record actual usage after the response.

Requirement 8 (integrity) falls out of the architecture: the model has no tools
and returns only schema-conforming JSON; document ids are assigned by code, not
the model; a single function with parameterised queries writes to the DB and can
only touch the row for the given id.

**Stage 8 — `report` and `eval`.** Compute the report from DB queries, not
in-memory counters. It then balances by construction, including after `SIGKILL`
— `not_started` is simply the `pending` rows. Cost from the config price table.
`eval` joins `expected.jsonl` to the DB through the `files` table and scores
each field after normalisation.

**Stage 9 — tests.** `pytest` with the `fake` backend. Cover: SIGKILL resume
(spawn a subprocess, wait for N completions, kill, resume, compare the record
set against a clean run, assert no repeated model calls for completed
documents); budget stop; report balance; unprocessable document; injection
leaves other records untouched; `--workers 1` vs `16` produce the same set;
memory on the huge file.

**Stage 10 — docs and dress rehearsal.** Fresh clone, Wi-Fi off. Time the full
run and `--limit 5`; peak memory via `/usr/bin/time -l`.

`ARCHITECTURE.md` (max one page) must state honestly:

- **Bottleneck:** one model on one GPU; generation is memory-bandwidth bound, so
  workers beyond the slot count just wait.
- **Known limitations:** no OCR, no fuzzy dedup, possible minor `summary`
  variation under server-side batching.
- **At 100× scale:** single-writer SQLite and one inference server stop being
  enough. Then: a task queue, Postgres, a server with real GPU batching.

---

## 9. Working with Claude Code

The brief explicitly expects AI assistants and says it doesn't affect scoring.
Claude Code runs locally, so it can generate data, run tests, kill processes for
the resume test, and time runs.

1. Put the brief in the repo as `docs/ZADANIE.md` and the corpus spec as
   `docs/DATA_SPEC.md`. **Point at the files rather than pasting them into
   chat** — they get re-read each step instead of decaying out of context.
2. `CLAUDE.md` holds the rules: tests never call a real model; every change must
   pass `pytest`; `ARCHITECTURE.md` must match the code.
3. Settle architecture in planning mode before implementing.
4. One stage per session, commit at the end. Acceptance test before code for
   every requirement.
5. Final pass as reviewer: *"walk requirements 1–10 and show where the code
   fails them"*, then *"check `ARCHITECTURE.md` against the code"*.

**Caveat:** the reviewers probe whether you know what your solution doesn't do.
Every decision has to be one you can defend unaided.

Realistically this exceeds the estimated "one to two evenings".

---

## 10. Sources worth rechecking before pinning

- Bielik releases and any 3.1 availability — Hugging Face (SpeakLeash).
- llama.cpp release tags and prebuilt binary names — GitHub Releases.
- AWS `mac2.metal` pricing and the 24-hour minimum — AWS EC2 Mac docs.
- GitHub Actions macOS arm64 runner specs and MPS limitation — GitHub docs.
- `uv` resolution and `--python-platform` behaviour — https://docs.astral.sh/uv/concepts/resolution/
