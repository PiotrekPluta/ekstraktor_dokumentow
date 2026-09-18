# ekstraktor_dokumentow

CLI, które wydobywa ustrukturyzowane dane (typ dokumentu, dane kontrahenta,
kwoty, walutę, streszczenie) z archiwum niestrukturyzowanych dokumentów przy
użyciu lokalnego modelu językowego.

## Instalacja

```
./setup.sh
```

Wymaga tylko `curl` (do zainstalowania [`uv`](https://docs.astral.sh/uv/),
jeśli nie ma go jeszcze w systemie) — `uv` sam pobiera wymaganą wersję
Pythona (3.12), więc nie trzeba mieć jej wcześniej zainstalowanej.

`setup.sh` instaluje zależności, pobiera przypięty model oraz binarkę
`llama-server` i **uruchamia backend inferencji** — po zakończeniu tego
kroku serwer już działa, żadna dodatkowa czynność nie jest potrzebna przed
`run`.

Sieć jest potrzebna wyłącznie na czas tego kroku.

## Uruchomienie

```
uv run extractor run    --input <katalog|zip> --db <plik.sqlite> [--workers N] [--limit N] [--budget N] [--config <plik>]
uv run extractor report --db <plik.sqlite> [--json]
uv run extractor eval   --db <plik.sqlite> --expected <expected.jsonl>
```

`run` dodatkowo samo-naprawia backend: jeśli skonfigurowany serwer
inferencji akurat nie odpowiada (np. padł po zakończeniu `setup.sh`),
uruchamia go ponownie tym samym, przypiętym modelem/binarką, zanim zacznie
przetwarzanie. Serwer uruchomiony ręcznie jest wykrywany i pozostaje
nietknięty.
