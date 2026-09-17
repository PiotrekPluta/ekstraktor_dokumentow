# ekstraktor_dokumentow

Zadanie rekrutacyjne polegające na utworzeniu narzędzia CLI, które będzie w stanie wydobyć z nieustrukturyzowanych dokumentów podstawowe informacje o kontrahentach.

## Setup

```
./setup.sh
```

Requires `curl` (to bootstrap [`uv`](https://docs.astral.sh/uv/) if it isn't
already installed). Network access is needed only for this step. For the
default (`llama_server`) backend, `setup.sh` also starts the pinned
inference server and leaves it running — no separate manual step is
needed before `run`.

## Run

```
uv run extractor run    --input <katalog|zip> --db <plik.sqlite> [--workers N] [--limit N] [--budget N] [--config <plik>]
uv run extractor report --db <plik.sqlite> [--json]
uv run extractor eval   --db <plik.sqlite> --expected <expected.jsonl>
```

`run` self-heals the backend: if the configured inference server isn't
already answering (e.g. it was never started, or died since `setup.sh`),
it starts it itself before processing, using the same pinned model/binary
`setup.sh` fetched. A server started by hand is left alone and used as-is.
