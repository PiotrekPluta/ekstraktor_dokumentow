# Architektura

Stan na koniec developmentu: wszystkie wymagania 1–10 z `docs/ZADANIE.md`
zaimplementowane. `run`/`report`/`eval` działają na rzeczywistym backendzie
(`llama_server`, domyślnie) i przechodzą testy bez sieci/modelu/klucza API.
Dane wejściowe: `data/corpus` (skorygowane synthetic archiwum), oczekiwane
wyniki: `data/expected.jsonl`, opis korpusu: `data/MANIFEST.md`.

## Kluczowe decyzje

- **Tożsamość dokumentu to sam hash deduplikacyjny**, nie sztuczny klucz:
  `documents.id` = sha256 znormalizowanego tekstu (lub surowych bajtów, gdy
  ekstrakcja się nie uda). Sortowanie po `id` daje deterministyczną
  kolejność przetwarzania za darmo.
- **Dwupoziomowy dedup**: najpierw sha256 bajtów (kopie 1:1), potem hash
  znormalizowanego tekstu (NFC, zwinięte białe znaki, bez linii-separatorów)
  — świadomie wąski, nie łączy niemal identycznych faktur z jednego szablonu.
- **Załącznik `.eml`, nie treść maila, staje się treścią dokumentu**, gdy
  jest realnym, wyodrębnialnym plikiem — nazwa załącznika nigdy nie buduje
  ścieżki na dysku (strukturalna ochrona przed path traversal).
- **Model przypięty do konkretnego commitu i sha256**:
  `speakleash/Bielik-4.5B-v3.0-Instruct-GGUF`, kwantyzacja `Q8_0` (~5,1GB),
  zweryfikowany bezpośrednio względem API Hugging Face. Binarka
  `llama-server` przypięta do tagu release'u `ggml-org/llama.cpp` (`b10985`)
  z lokalnie policzonym sha256 (llama.cpp nie publikuje sum kontrolnych).
- **`tokenizer.json` musi być lokalnie pobrany i wpisany do repo w
  `assets/tokenizer.json`, a nie pobierany przez `setup.sh`.** Bazowe (nie
  -GGUF) repo Bielika na Hugging Face, które udostępnia ten plik, jest
  zamknięte (gated) — `scripts/fetch_runtime.py` nie ma jak się do niego
  nieinteraktywnie uwierzytelnić. Plik ma ~3,7MB, więc został pobrany raz
  ręcznie i scommitowany wprost; dzięki temu każdy świeży klon ma dokładne,
  offline liczenie tokenów bez żadnej konfiguracji. Zapasowa ścieżka
  (`/tokenize` na żywym serwerze) zostaje dla forka bez `assets/`.
- **Backend wybierany wyłącznie przez konfigurację** (`llama_server` /
  `ollama` / `fake`), zero zmian w kodzie. Tag Ollamy
  (`speakleash/bielik-4.5b-v3.0-instruct:q8_0`) zweryfikowany względem
  registry Ollamy — ten sam sha256 wag co GGUF dla `llama_server`.
- **Retry + circuit breaker to jeden wspólny dekorator** (`ResilientLLMClient`)
  wokół każdego backendu, nie osobna logika per backend. Otwarty breaker nie
  ma trybu "half-open" w trakcie runu — odzyskanie następuje dopiero przy
  wznowieniu (nowy klient, nowy breaker). Bezpośrednio realizuje wymaganie 5:
  chwilowa niedostępność backendu kończy run z `stop_reason=backend_unavailable`,
  a dokument w locie wraca do `pending`, nigdy nie ginie.
- **Ekstrakcja tekstu izolowana per plik, w osobnym procesie z timeoutem**
  (`multiprocessing`, wymuszone `spawn` wszędzie — jak domyślnie na macOS):
  zawieszony wywołanie C wewnątrz `pypdfium2` nie da się przerwać na
  poziomie Pythona inaczej niż zabijając cały proces.
- **Budżet kontekstu okna (`windowing`) liczony per uruchomienie**, nie na
  sztywno: odejmuje realny narzut promptu systemowego i podwójny
  `max_output_tokens` (rezerwa na turę naprawczą) od `context_tokens`
  backendu, zanim cokolwiek trafi do modelu.
- **Schemat JSON trafia do modelu strukturalnie** (parametr `json_schema`,
  wymuszona gramatyka po stronie serwera), nigdy jako wklejony tekst w
  promptcie — wklejona wersja sama w sobie zajmowała więcej miejsca niż
  cały budżet kontekstu serwera.
- **Waluta: allowlist ISO 4217 plus tylko jednoznaczne symbole** (`zł`,
  `€`, `£`) — samo `$` nigdy nie jest automatycznie mapowane na USD, bo bez
  kontekstu sprzedawcy jest wieloznaczne (potwierdzone realnym przypadkiem
  w `data/expected.jsonl`, gdzie `$` bez adresu sprzedawcy daje `null`).
- **Błędna suma kontrolna NIP jest tylko informacyjna**, nigdy nie odrzuca
  ani nie zeruje wartości pola.
- **`--limit`/`--budget` są kumulatywne w całej historii bazy**, nie per
  wywołanie — jedyna interpretacja spójna z wymaganiem 4 (ten sam zestaw
  rekordów niezależnie od liczby przerwań). Rezerwacje tokenów w
  `token_ledger` nigdy nie są zwalniane, nawet po zabiciu procesu w trakcie
  wywołania — budżet nigdy nie zostanie realnie przekroczony.
- **Ochrona przed wstrzyknięciem treści jest strukturalna, nie treściowa**:
  każdy zapis do bazy dotyczy wyłącznie wiersza jednego, konkretnego
  dokumentu (parametryzowane zapytania, `document_id` nadawany przez kod,
  nie przez model). Sprawdzanie „czy wartość występuje w źródle" jest
  dodatkową, ale świadomie niewystarczającą samodzielnie warstwą (patrz
  ograniczenia).
- **`extractor.llm.lifecycle` sam uruchamia i pilnuje `llama-server`**:
  `setup.sh` startuje serwer i wysyła jedno prawdziwe zapytanie testowe;
  `extractor run` samo-naprawia backend, jeśli serwer nie odpowiada. Zawsze
  ustawia `--ctx-size` jako `context_tokens * liczba_workerów` i tyle samo
  `--parallel`, eliminując strukturalnie błąd współdzielonego KV-cache
  między slotami znaleziony podczas realnych testów.
- **Testy end-to-end na prawdziwym macOS arm64** (GitHub Actions,
  `macos-14` — maszyna oceniająca to też macOS/arm64) wykryły i naprawiły
  4 realne błędy niewidoczne wcześniej na maszynie deweloperskiej: brak
  grupy zależności `datagen` w `setup.sh` (zestaw testów w ogóle się nie
  zbierał), złe jednostki `ru_maxrss` na macOS (bajty, nie kilobajty),
  brak plików `data/corpus/huge_log_*` na świeżym klonie (teraz
  generowane automatycznie, jeśli ich brak) oraz brak fontu DejaVu Sans na
  macOS w generatorze PDF (font zvendorowany wprost do repo,
  `scripts/generator/assets/`).

## Znane ograniczenia

- Brak OCR — zeskanowany PDF bez warstwy tekstowej trafia do kwarantanny.
- Zagnieżdżone załączniki `.eml` w `.eml` nie są śledzone.
- Progi retry/backoff/circuit-breakera są zahardkodowane, nieskonfigurowalne
  przez plik konfiguracyjny (poza timeoutem pojedynczego zapytania).
- Cykl życia procesu Ollamy (start/stop, pobranie modelu) pozostaje w
  całości ręczny — `fetch_runtime.py`/`setup.sh` obsługują tylko
  `llama_server`; recenzent przełączający się na Ollamę musi sam odpalić
  `ollama serve`/`ollama pull`.
- Brak porównania rzeczywiście uruchomionego modelu z przypiętym digestem
  przy starcie (`vendor/versions.lock` jest zapisywany, ale nic go potem
  nie odczytuje jako bramkę bezpieczeństwa).
- Sprawdzenie „wartość występuje w źródle" nie chroni przed spreparowanym,
  fałszywym blokiem JSON osadzonym w treści dokumentu — rzeczywistą
  ochronę integralności (wymaganie 8) daje wyłącznie zapis ograniczony do
  jednego wiersza, nie ta walidacja.
- `eval`: metryka `summary` nie sprawdza języka wyniku; `nip_checksum_valid`
  jest napisany, ale nigdzie niewpięty do raportu (dałby dużo fałszywych
  alarmów na zagranicznych kontrahentach).
- `--workers 16` przeciwko prawdziwemu `llama_server` nie był testowany
  automatycznie (tylko przeciw `fake`) — bezpieczna górna granica
  równoległości zależy od tego, jak wystartowano serwer.
- `wall_time_s` dla przerwanego (niezakończonego) runu jest przybliżeniem
  (czas ostatniego wpisu w `token_ledger`), nie realnym momentem zabicia
  procesu — nie do odzyskania po fakcie.
- Wymaganie 9 (2GB pamięci) jest właściwością projektu (streaming,
  ograniczona liczba workerów trzymających dane naraz), nie mechanizmem
  egzekwowanym w runtime — zmierzone ręcznie na maszynie oceniającej.

## Wąskie gardło przepustowości (wymaganie 5)

Jeden serwer inferencji, jeden model na CPU/GPU — przetwarzanie promptu
jest compute-bound, więc równoległe sloty serwera pomagają tylko do pewnego
progu; należy zakładać przepustowość bliską sekwencyjnej. Największy wpływ
na czas odpowiedzi ma rozmiar kontekstu wysyłanego do modelu, nie rozmiar
samego modelu — stąd okienkowanie długich dokumentów zamiast wysyłania
całego tekstu. `--workers` kontroluje równoległość wszystkiego *poza*
samym wywołaniem modelu — nie zwiększa liczby równoległych zapytań do
serwera, bo to osobny parametr po stronie backendu (liczba slotów). Serwer
uruchamiany przez `extractor.llm.lifecycle` zawsze ma `--parallel` równe
liczbie workerów, więc te dwie wartości nie mogą się rozjechać.

## Przy 100-krotnie większym archiwum

Jednowątkowy zapis SQLite (WAL serializuje zapisy) i jeden serwer
inferencji przestają wystarczać dużo wcześniej niż przy 100× większym
archiwum. Co by się zmieniło: prawdziwa kolejka zadań zamiast workerów
odpytujących jeden plik bazy, Postgres zamiast SQLite, i grupowanie
(batching) zapytań do modelu zamiast jednego dokumentu na wywołanie.
Deduplikacja na poziomie bajtów skaluje się liniowo już teraz (strumieniowe
sha256, bez porównań parami) — tu nic nie trzeba zmieniać.

Odpalanie osobnego procesu na plik przy ekstrakcji kosztuje ok. 300ms
(start interpretera, ponowny import `pypdfium2`/`python-docx`) — przy 40
plikach nieistotne, przy 4000 plikach to już ok. 20 minut samego narzutu
startowego przed jakimkolwiek parsowaniem. Przy takiej skali potrzebna
byłaby trwała pula workerów zamiast nowego procesu na plik, kosztem części
izolacji.

Pętla orkiestracji (jedno połączenie SQLite na wątek, krótkie transakcje
serializowane przez WAL + `busy_timeout`) jest wystarczająca przy 40
dokumentach i do 16 workerów. Przy 100× skali, z dużo większą liczbą
workerów potrzebną do nakarmienia backendu zdolnego do batchowania,
jednowątkowy zapis SQLite stałby się wąskim gardłem *przed* samym serwerem
inferencji — to ta sama zmiana na Postgres co wyżej, tu uzasadniona
konkretnie wzorcem zapisu z orkiestracji, nie abstrakcyjnie.
