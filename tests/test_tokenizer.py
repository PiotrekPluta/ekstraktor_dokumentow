"""Against the real committed assets/tokenizer.json — no network involved
(it's a local file checked into the repo, not fetched), so this is exactly
as offline as every other test here despite exercising the real asset.
"""

from __future__ import annotations

from pathlib import Path

from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from extractor.tokenizer import DEFAULT_TOKENIZER_PATH, count_tokens, load_tokenizer


def test_default_tokenizer_path_exists() -> None:
    assert DEFAULT_TOKENIZER_PATH.exists()


def test_load_tokenizer_default_path() -> None:
    tok = load_tokenizer()
    assert tok.get_vocab_size() > 0


def test_count_tokens_positive_for_real_text() -> None:
    tok = load_tokenizer()
    assert count_tokens(tok, "Faktura VAT nr FV/2024/03/018.") > 0


def test_count_tokens_is_deterministic() -> None:
    tok = load_tokenizer()
    text = "Sprzedawca: Test Sp. z o.o. NIP: 5260001246."
    assert count_tokens(tok, text) == count_tokens(tok, text)


def test_count_tokens_grows_with_longer_text() -> None:
    tok = load_tokenizer()
    short = count_tokens(tok, "Faktura")
    long = count_tokens(
        tok, "Faktura VAT nr FV/2024/03/018 wystawiona przez Sprzedawcę"
    )
    assert long > short


def test_count_tokens_empty_string_does_not_crash() -> None:
    tok = load_tokenizer()
    assert count_tokens(tok, "") >= 0


def _build_tiny_tokenizer(path: Path) -> None:
    tok = Tokenizer(
        WordLevel(vocab={"[UNK]": 0, "hello": 1, "world": 2}, unk_token="[UNK]")
    )
    tok.pre_tokenizer = Whitespace()
    tok.save(str(path))


def test_load_tokenizer_respects_custom_path(tmp_path: Path) -> None:
    custom_path = tmp_path / "tiny.json"
    _build_tiny_tokenizer(custom_path)

    tok = load_tokenizer(custom_path)
    assert count_tokens(tok, "hello world") == 2
    assert count_tokens(tok, "hello world foo") == 3


def test_offline_tokenizer_works_as_windowing_count_tokens_callable() -> None:
    from functools import partial

    from extractor.windowing import DEFAULT_TOKEN_BUDGET, build_context

    tok = load_tokenizer()
    filler = "Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 400

    result = build_context(
        filler,
        count_tokens=partial(count_tokens, tok),
        token_budget=DEFAULT_TOKEN_BUDGET,
    )
    assert result
    assert len(result) < len(filler)
