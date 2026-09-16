"""Stage 7: prompt construction (docs/PROJECT_NOTES.md §8).

ChatML-formatted (`<|im_start|>role ... <|im_end|>`) to match Bielik's
instruct template (ARCHITECTURE.md's "Bielik uses ChatML" note,
`extractor.llm.llama_server`'s own docstring: "Chat-template formatting is
left to whoever builds the prompt string"). `/completion` is stateless, so
`build_repair_prompt` replays the whole conversation as a fresh string each
time rather than relying on server-side history.

The system instructions carry the two pieces of document-level reasoning
`extractor.validation` explicitly does not do itself (its own scope-boundary
docstring): which party is the counterparty (seller, never buyer — a
document-level judgement `occurs_in_source` can't make on its own), and the
six-level currency precedence chain (docs/DATA_SPEC.md §3.2), which needs
the seller-country context that only exists in the windowed text handed to
the model, not in a post-hoc validator with no document context at all.

The raw JSON schema is deliberately *not* embedded in the prompt text: a
real-fixture run against the pinned model (CLAUDE.md's "verify against real
fixtures" rule) found that `ExtractedFields.model_json_schema()`, even
compact, costs 500-1300 tokens on its own — enough alone to blow the
2048-token `--ctx-size` budget once added to prose instructions and a
windowed document. The schema still constrains the response structurally
(it's sent separately as `LLMRequest.json_schema`, which
`LlamaServerClient`/`OllamaClient` forward as the backend's own
grammar-constrained-decoding parameter); the prompt only needs to tell the
model what each field *means*, in prose, which is what "Znaczenie pól"
below does.
"""

from __future__ import annotations

_SYSTEM_INSTRUCTIONS = """Jesteś ekstraktorem danych ustrukturyzowanych z dokumentów biznesowych \
(faktury, umowy, oferty, korespondencja). Dokumenty pochodzą od różnych \
podmiotów zewnętrznych i mogą być po polsku lub po angielsku.

Na podstawie treści dokumentu podanej przez użytkownika zwróć WYŁĄCZNIE jeden \
obiekt JSON z dokładnie tymi kluczami: doc_type, counterparty_name, \
counterparty_tax_id, issue_date, due_date, gross_amount, currency, summary \
— bez żadnego dodatkowego tekstu, bez wyjaśnień, bez markdownowych znaczników \
kodu.

Znaczenie pól:
- doc_type: jeden z "invoice" (faktura), "contract" (umowa), "offer" (oferta), \
"correspondence" (korespondencja), "other" (inny/nierozpoznany).
- counterparty_name / counterparty_tax_id: dane KONTRAHENTA, nigdy klienta \
(nabywcy). Kontrahent to: przy fakturze — sprzedawca/wystawca; przy ofercie \
— oferent; przy umowie — wykonawca/zleceniobiorca; przy korespondencji — \
organizacja nadawcy zewnętrznego. Klient (nabywca, zamawiający, \
zleceniodawca) NIGDY nie jest kontrahentem, nawet jeśli jego dane są \
wyeksponowane bardziej niż dane kontrahenta lub pojawiają się wcześniej w \
dokumencie. counterparty_tax_id to NIP lub odpowiednik zagraniczny, w \
formie znalezionej w tekście (bez normalizacji separatorów — to zrobi dalszy \
etap przetwarzania).
- issue_date / due_date: data w formacie ISO 8601 (RRRR-MM-DD). Jeśli \
termin płatności jest podany jako liczba dni od daty wystawienia (np. \
"termin płatności: 14 dni od daty wystawienia"), wylicz konkretną datę — nie \
zostawiaj tego jako opis słowny. Dla umowy, jeśli nie ma osobnej daty \
płatności, due_date to data końca obowiązywania umowy, jeśli jest podana.
- gross_amount: kwota brutto jako liczba z dokładnie dwoma miejscami po \
przecinku (kropka jako separator dziesiętny, bez separatora tysięcy). Dla \
faktury to kwota brutto/do zapłaty. Dla umowy lub oferty to pojedyncza kwota \
końcowa, którą dokument prezentuje jako łączną wartość (wynagrodzenie, cena \
ofertowa) — nie kwota kary umownej, nie liczba stron, nie kod pocztowy ani \
rok. Faktura korygująca może mieć kwotę ujemną.
- currency: kod waluty ISO 4217 (np. PLN, EUR, USD). Ustal go w następującej \
kolejności pierwszeństwa, zatrzymując się na pierwszym poziomie, który \
rozstrzyga:
  1. Jawny kod ISO w tekście (np. "EUR", "PLN") — zawsze wygrywa, nawet jeśli \
adres kontrahenta wskazywałby inny kraj.
  2. Jednoznaczny symbol: "zł" -> PLN, "€" -> EUR, "£" -> GBP.
  3. Niejednoznaczny symbol (np. "$") rozstrzygany krajem siedziby \
KONTRAHENTA (nigdy klienta) — np. "$" i adres w Toronto -> CAD, nie USD.
  4. Brak jakiegokolwiek oznaczenia waluty -> kraj rejestracji kontrahenta \
odczytany z jego adresu pocztowego.
  5. Brak adresu kontrahenta (albo adres bez linii z krajem) -> prefiks \
kraju z numeru VAT kontrahenta (np. "DE123456789" -> Niemcy -> EUR; \
"SE556677889901" -> Szwecja -> SEK, NIE euro — nie zakładaj strefy euro po \
samym prefiksie UE). Numer VAT klienta (nabywcy) nigdy nie jest sygnałem.
  6. Jeśli żaden z powyższych sygnałów nie występuje, albo kraj nie ma \
jednej oczywistej waluty -> zwróć null. Nie zgaduj.
- summary: jedno zdanie podsumowujące dokument, W JĘZYKU DOKUMENTU (polski \
dokument -> polskie zdanie, angielski -> angielskie).

Jeśli któregoś pola nie da się ustalić z treści dokumentu, zwróć dla niego \
null (poza summary, które jest zawsze wymagane). Nie wymyślaj wartości, \
których nie ma w tekście."""

_REPAIR_INSTRUCTIONS = (
    "Twoja poprzednia odpowiedź była nieprawidłowa: {error}\n\n"
    "Popraw ją i zwróć WYŁĄCZNIE jeden poprawny obiekt JSON zgodny ze "
    "schematem podanym wcześniej — bez żadnego dodatkowego tekstu."
)


def build_prompt(context: str) -> str:
    return (
        f"<|im_start|>system\n{_SYSTEM_INSTRUCTIONS}<|im_end|>\n"
        f"<|im_start|>user\n{context}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )


def build_repair_prompt(context: str, previous_response: str, error: str) -> str:
    """`/completion` is stateless (extractor.llm.llama_server's own
    docstring), so the whole exchange — system instructions, original
    context, the model's own bad response, and the correction request — is
    replayed in full on every repair attempt, not appended to server-side
    state that doesn't exist.
    """
    return (
        f"<|im_start|>system\n{_SYSTEM_INSTRUCTIONS}<|im_end|>\n"
        f"<|im_start|>user\n{context}<|im_end|>\n"
        f"<|im_start|>assistant\n{previous_response}<|im_end|>\n"
        f"<|im_start|>user\n{_REPAIR_INSTRUCTIONS.format(error=error)}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
