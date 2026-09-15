# Working rules for this repository

This project is built stage-by-stage per `docs/PROJECT_NOTES.md` §8. Read
`docs/ZADANIE.md` (the brief) and `docs/DATA_SPEC.md` (corpus spec) by
reference — don't paste their content into a session; point at the files
so they get re-read each step instead of decaying out of context.

## Rules

- **Tests never call a real model, hit the network, or need an API key.**
  The `fake` backend exists exactly for this; requirement 10 demands the
  test suite pass with none of the three available.
- **Every change must pass `uv run pytest` and `uv run ruff check` /
  `ruff format --check`** before it's done, not before it's committed.
- **`ARCHITECTURE.md` must always match the code.** Update it in the same
  session as the change it documents. Don't cap it while the solution is
  still being built — record decisions and limitations in full as they
  happen; the one-page limit (`docs/ZADANIE.md`'s submission requirement)
  is a final-pass trim, not a constraint on every session (see below).
- **One stage per session, commit at the end.** Stages are defined in
  `docs/PROJECT_NOTES.md` §8. Settle the approach — design decisions,
  ambiguous requirements — before writing code, not while writing it.
- **Write the acceptance test before (or alongside) the code**, for every
  requirement a stage touches. The ten requirements in `docs/ZADANIE.md`
  are acceptance criteria, not a checklist to satisfy retroactively.
- **Verify against real fixtures, not only synthetic examples.**
  `data/corpus` is small enough that a claim about extraction, dedup, or
  windowing behaviour should be checked against the actual generated
  files. Several real bugs here were only found this way, not by
  reasoning about the code in isolation.
- **Every non-obvious decision needs a stated reason** — in a code comment
  or `ARCHITECTURE.md`, not only in whatever session produced it. The
  brief's reviewers explicitly probe "what does your solution not do";
  that has to be answerable from the repo, unaided.

## Before final submission

Do a reviewer pass: walk requirements 1–10 in `docs/ZADANIE.md` and state
where the code satisfies each one and where it doesn't, then check
`ARCHITECTURE.md` against the actual code. Repeat whenever a change
plausibly shifts what an earlier requirement's answer would be — not a
one-time step at the very end.

Only then trim `ARCHITECTURE.md` down to one page — cut illustrative
detail and color commentary first, keep every decision, limitation, and
the two required sections (throughput bottleneck, 100× scale) intact.
