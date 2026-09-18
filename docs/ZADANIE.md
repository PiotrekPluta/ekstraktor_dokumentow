# Zadanie rekrutacyjne: ekstraktor danych z archiwum dokumentów

## Kontekst

Klient przekazał nam archiwum dokumentów zgromadzonych przez kilka lat: faktury, umowy, oferty, korespondencję. Pochodzą od wielu podmiotów zewnętrznych, w różnych formatach i różnej jakości. Klient chce z nich wyciągnąć ustrukturyzowane dane do dalszego przetwarzania.

Twoim zadaniem jest napisanie narzędzia, które to zrobi. Narzędzie ma korzystać z modelu językowego do interpretacji treści dokumentów.

Interesuje nas wyłącznie efekt: działające narzędzie i jego zachowanie w warunkach opisanych poniżej. Sposób dojścia do celu, w tym użycie asystentów AI przy implementacji, jest Twoją sprawą.

## Dane wejściowe

Dane przygotowujesz sam, w dowolny sposób. Oddajesz je razem z projektem (patrz „Co oddajesz").

Archiwum, na którym ocenimy Twoje narzędzie, będzie miało poniższą charakterystykę. Twoje dane powinny do niej pasować.

- Około 40 plików. Dane syntetyczne.
- Formaty: `.eml` (w tym z załącznikami), `.pdf`, `.docx`, `.html`, `.txt`.
- Języki: głównie polski, część po angielsku.
- Kodowania: różne, w tym starsze kodowania jednobajtowe.
- Rozmiary: od jednego akapitu do kilkuset stron. Co najmniej jeden plik ma kilkaset megabajtów.
- Ten sam dokument może występować wielokrotnie pod różnymi nazwami plików, w różnych kodowaniach lub z drobnymi różnicami formatowania.
- Część plików jest uszkodzona lub nie zawiera dokumentu żadnego z rozpoznawanych typów.
- Treść realistyczna: faktury z pozycjami i podsumowaniem, umowy z klauzulami, korespondencja z prośbami i poleceniami adresowanymi do odbiorcy.

Ocena odbędzie się na **naszym** archiwum, nie na Twoim. Rozwiązanie dopasowane do konkretnych plików nie przejdzie.

## Co ma powstać

Narzędzie CLI, które dla każdego unikalnego dokumentu w archiwum ustala:

| Pole | Typ | Uwagi |
|---|---|---|
| `doc_type` | enum | `invoice`, `contract`, `offer`, `correspondence`, `other` |
| `counterparty_name` | string \| null | nazwa kontrahenta |
| `counterparty_tax_id` | string \| null | NIP lub odpowiednik, znormalizowany (bez separatorów) |
| `issue_date` | date \| null | ISO 8601 |
| `due_date` | date \| null | ISO 8601 |
| `gross_amount` | decimal \| null | kwota brutto, dwa miejsca po przecinku |
| `currency` | string \| null | ISO 4217 |
| `summary` | string | jedno zdanie, w języku dokumentu |

Każde pole ma zostać wyciągnięte niezależnie od tego, w którym miejscu dokumentu się znajduje.

Wyniki zapisujesz do pojedynczego pliku SQLite.

## Wymagania

Poniższe punkty są kryteriami akceptacji. Każdy z nich sprawdzimy.

### 1. Uruchomienie

Jedna komenda przygotowuje środowisko, jedna uruchamia przetwarzanie. `README.md` opisuje obie i nic więcej nie jest potrzebne. Jeśli coś ma być zainstalowane wcześniej (runtime języka, serwer inferencji), README mówi co i w jakiej wersji.

Dowolny język i stack. Docker dozwolony, nieobowiązkowy.

### 2. Model i backend

Dobór modelu językowego i sposobu jego uruchomienia należy do Ciebie. Warunki:

- Domyślna konfiguracja działa **bez klucza do żadnej płatnej usługi** na maszynie oceniającej (opis niżej) i mieści się w jej pamięci razem z narzędziem.
- Backend i model są wybierane przez plik konfiguracyjny, bez zmian w kodzie. Konfiguracja obsługuje co najmniej dwa różne backendy inferencji.
- Model jest przypięty do konkretnej wersji (tag oraz digest lub równoważny identyfikator). Oceniamy dokładnie na tym, co przypiąłeś.

### 3. Interfejs

```
<tool> run    --input <katalog|zip> --db <plik.sqlite> [--workers N] [--limit N] [--budget N] [--config <plik>]
<tool> report --db <plik.sqlite> [--json]
<tool> eval   --db <plik.sqlite> --expected <expected.jsonl>
```

- `--workers N`: liczba równoległych jednostek przetwarzania, `N ≥ 1`.
- `--limit N`: przetwórz co najwyżej N dokumentów.
- `--budget N`: maksymalna łączna liczba tokenów (wejście + wyjście), jaką narzędzie może zużyć w danym uruchomieniu.
- `report` wypisuje raport opisany w punkcie 7.
- `eval` porównuje zawartość bazy z oczekiwanymi wynikami i wypisuje skuteczność ekstrakcji per pole. Format `expected.jsonl`: jeden wiersz na unikalny dokument, pola jak w tabeli wyżej plus `files` (lista ścieżek plików, które ten dokument reprezentują). Pliki uszkodzone i bez rozpoznawalnego dokumentu mają `doc_type` równe `null`.

### 4. Przerwania

Proces może zostać zabity w dowolnym momencie sygnałem `SIGKILL`. Ponowne uruchomienie z tymi samymi parametrami dokończy pracę.

Po dowolnej liczbie przerwań i wznowień zbiór rekordów w bazie jest identyczny ze zbiorem z przebiegu nieprzerwanego. Żaden dokument zakończony przed przerwaniem nie jest przetwarzany ponownie, w szczególności nie generuje ponownych wywołań modelu.

### 5. Równoległość

Zbiór rekordów wynikowych nie zależy od wartości `--workers`. Narzędzie działa stabilnie dla `N = 1` i `N = 16` na maszynie oceniającej. Nie wymagamy liniowego przyrostu wydajności. Wymagamy, żeby `ARCHITECTURE.md` opisywał, co w Twojej konfiguracji realnie ogranicza przepustowość i dlaczego.

Chwilowa niedostępność, przeciążenie lub timeout backendu nie może skutkować utratą dokumentu ani zawieszeniem narzędzia bez końca.

### 6. Budżet

Przekroczenie `--budget` jest niedopuszczalne. Narzędzie kończy pracę **zanim** do niego dojdzie, a raport podaje przyczynę zatrzymania.

Raport zawiera zużycie tokenów oraz szacowany koszt wyliczony z tabeli cen (per model, per milion tokenów wejścia i wyjścia). Tabela cen jest częścią konfiguracji. Dla backendu lokalnego koszt wynosi zero, mechanizm ma działać tak samo.

### 7. Raport

`report --json` zwraca co najmniej:

```
input_files, duplicate_files, unique_documents,
processed_ok, quarantined, quarantine_by_reason{},
tokens_in, tokens_out, estimated_cost, wall_time_s,
backend, model, stop_reason
```

Raport musi się bilansować: `input_files = duplicate_files + unique_documents` oraz `unique_documents = processed_ok + quarantined + not_started`.

Dokument, którego nie udało się przetworzyć poprawnie, trafia do kwarantanny z czytelną przyczyną. Kwarantanna nie jest błędem narzędzia. Cicha utrata dokumentu jest.

### 8. Integralność danych

Treść dokumentów pochodzi od podmiotów zewnętrznych i nie jest zaufana. Niezależnie od zawartości przetwarzanego dokumentu narzędzie może tworzyć lub modyfikować wyłącznie rekord dotyczący tego dokumentu. Żadna inna operacja na bazie nie może być skutkiem treści dokumentu.

### 9. Zasoby

Szczytowe zużycie pamięci procesu przy `--workers 4` nie przekracza 2 GB, niezależnie od rozmiaru pojedynczego pliku wejściowego.

### 10. Testy

Zestaw testów uruchamiany jedną komendą przechodzi na maszynie **bez dostępu do sieci, bez klucza API i bez uruchomionego serwera inferencji**.

Testy pokrywają co najmniej: wznowienie po przerwaniu, zatrzymanie budżetem, bilansowanie raportu, dokument nienadający się do przetworzenia.

## Maszyna oceniająca

- macOS, Apple M1, 16 GB pamięci wspólnej, dysk SSD.
- Serwer inferencji uruchamiamy natywnie, według Twojego README. Docker jest dozwolony dla narzędzia, ale pamiętaj, że na macOS kontenery nie mają dostępu do GPU.
- Pełny przebieg na archiwum oceniającym z Twoją domyślną konfiguracją i `--workers 4` ma się zakończyć w czasie do 20 minut. `--limit 5` w czasie do 3 minut.
- Sieć dostępna wyłącznie podczas kroku instalacji. Podczas `run` i podczas testów sieć jest odcięta.

## Co oddajesz

Link do repozytorium albo archiwum zip. Zawartość:

- kod źródłowy,
- `README.md` (uruchomienie, nic więcej),
- `ARCHITECTURE.md`, maksymalnie jedna strona: kluczowe decyzje, znane ograniczenia, co się zepsuje przy 100× większym archiwum i co byś wtedy zmienił,
- konfiguracja domyślna z przypiętym modelem,
- dane wejściowe, na których pracowałeś, wraz z `expected.jsonl` dla nich. Jeśli któryś plik jest za duży do przesłania, wystarczy skrypt, który go odtwarza.

Nie oddajesz bazy wynikowej ani logów. Wygenerujemy je sami.

## Jak oceniamy

Zaczynamy od Twoich danych: sprawdzamy, czy odpowiadają charakterystyce z sekcji „Dane wejściowe" i czego próbują od narzędzia wymagać.

Potem odpalamy Twoje narzędzie według README na naszym archiwum, przerywamy je, wznawiamy, zmieniamy parametry, czytamy raport, zaglądamy do bazy. Uruchamiamy `eval` na Twoich danych i na naszych. Uruchamiamy testy z odciętą siecią. Sprawdzamy też zachowanie na archiwum liczącym kilka tysięcy plików, z których zdecydowana większość to duplikaty.

Najbardziej interesuje nas, czy narzędzie zachowuje się tak, jak mówi jego raport, czy `ARCHITECTURE.md` opisuje to, co faktycznie jest w kodzie, i czy wyniki `eval` na Twoich danych mówią to samo, co na naszych.

Nie oczekujemy perfekcji. Oczekujemy, że wiesz, czego Twoje rozwiązanie nie robi.

## Praktycznie

- Szacowany nakład: jeden do dwóch wieczorów.
- Korzystanie z asystentów AI przy implementacji jest oczekiwane i nie wpływa na ocenę.
- Pytania do treści zadania:
 Nie odpowiadamy na pytania „czy X wystarczy". Odpowiadamy na pytania o niejasności w wymaganiach.
