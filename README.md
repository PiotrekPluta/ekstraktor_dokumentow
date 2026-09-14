# ekstraktor_dokumentow

Zadanie rekrutacyjne polegające na utworzeniu narzędzia CLI, które będzie w stanie wydobyć z nieustrukturyzowanych dokumentów podstawowe informacje o kontrahentach.

## Setup

```
./setup.sh
```

Requires `curl` (to bootstrap [`uv`](https://docs.astral.sh/uv/) if it isn't
already installed). Network access is needed only for this step.

## Run

```
uv run extractor run    --input <katalog|zip> --db <plik.sqlite> [--workers N] [--limit N] [--budget N] [--config <plik>]
uv run extractor report --db <plik.sqlite> [--json]
uv run extractor eval   --db <plik.sqlite> --expected <expected.jsonl>
```

**Status:** project skeleton only (see `docs/PROJECT_NOTES.md` §8, Stage 0).
`run` / `report` / `eval` parse their arguments and load `config/`, but do
not yet process documents — each exits with a "not implemented" message.
