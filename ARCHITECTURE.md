# Architektura

Stan na koniec developmentu: 
- `run`/`report`/`eval` działają na rzeczywistym backendzie (`llama_server`, domyślnie) i przechodzą testy bez sieci/modelu/klucza API.
- Dane wejściowe syntetyczne: `data/corpus`
- Oczekiwane wyniki: `data/expected.jsonl`, 
- Opis danych wejściowych: `data/MANIFEST.md`.

## Kluczowe decyzje

- **ID dokumentu to sam hash deduplikacyjny**
  `documents.id` = sha256 znormalizowanego tekstu (lub surowych bajtów, gdy ekstrakcja się nie uda). Sortowanie po `id` daje deterministyczną
  kolejność przetwarzania.

- **Model i server przypięte do konkretnego commitu i sha256**:
  `speakleash/Bielik-4.5B-v3.0-Instruct-GGUF`, kwantyzacja `Q8_0` (~5,1GB), zweryfikowany bezpośrednio względem API Hugging Face. 
  Binarka `llama-server` przypięta do tagu release'u `ggml-org/llama.cpp` (`b10985`)
- **`tokenizer.json`** jest wykorzystywany do obliczania tokenów a nie narzędzie dostarczane przez backend aby możliwe było przełączenie się między backendami.

- **Backend wybierany wyłącznie przez konfigurację** (`llama_server` /
  `ollama` / `fake`)

- **Retry + circuit breaker to jeden wspólny dekorator** (`ResilientLLMClient`) wokół każdego backendu, nie osobna logika per backend. 
- **Ekstrakcja tekstu izolowana per plik, w osobnym procesie z timeoutem**
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
  wywołanie
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

## Znane ograniczenia

- Brak OCR — zeskanowany PDF bez warstwy tekstowej trafia do kwarantanny.
- Zagnieżdżone załączniki `.eml` w `.eml` nie są śledzone.
- Progi retry/backoff/circuit-breakera są zahardkodowane, nieskonfigurowalne
  przez plik konfiguracyjny (poza timeoutem pojedynczego zapytania).
- Sprawdzenie „wartość występuje w źródle" nie chroni przed spreparowanym,
  fałszywym blokiem JSON osadzonym w treści dokumentu 
- `eval`: metryka `summary` nie sprawdza języka wyniku; `nip_checksum_valid`
  jest napisany, ale nigdzie niewpięty do raportu (dałby dużo fałszywych
  alarmów na zagranicznych kontrahentach).
- `--workers 16` przeciwko prawdziwemu `llama_server` nie był testowany
  automatycznie (tylko przeciw `fake`) — bezpieczna górna granica
  równoległości zależy od tego, jak wystartowano serwer.

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
