from __future__ import annotations

import pytest

from sidelamp.search.tokenizer import singular, term_frequencies, tokenize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Growth was 3.1 percent", ["growth", "3.1", "percent"]),
        ("Revenue of 1,234,567.50 dollars", ["revenue", "1234567.50", "dollar"]),
        ("Taux de 3,5 %", ["taux", "de", "3.5"]),
        ("Order AB1234 shipped", ["order", "ab1234", "shipped"]),
        ("Certified ISO-27001 and covid-19 rules", ["certified", "iso-27001", "covid-19", "rule"]),
        ("Due on 2024-01-03", ["due", "2024-01-03"]),
        ("A well-known fact", ["well", "known", "fact"]),
    ],
)
def test_codes_and_numbers_stay_whole(text: str, expected: list[str]) -> None:
    assert tokenize(text) == expected


def test_lowercase_and_accent_folding() -> None:
    assert tokenize("Café CRÈME Brûlée") == ["cafe", "creme", "brulee"]


@pytest.mark.parametrize(
    ("plural", "single"),
    [
        ("policies", "policy"),
        ("boxes", "box"),
        ("churches", "church"),
        ("reports", "report"),
        ("classes", "class"),
        ("status", "status"),
        ("analysis", "analysis"),
        ("bus", "bus"),
        ("series", "series"),
        ("glass", "glass"),
        ("famous", "famous"),
    ],
)
def test_simple_plurals(plural: str, single: str) -> None:
    assert singular(plural) == single


def test_plural_and_singular_match() -> None:
    assert tokenize("refund policies") == tokenize("Refund policy")


def test_stop_words_and_contractions_dropped() -> None:
    assert tokenize("What is the refund window for the store's customers? Don't guess.") == [
        "refund",
        "window",
        "store",
        "customer",
        "guess",
    ]
    assert "the" in tokenize("the end", keep_stop_words=True)


def test_single_letters_dropped_but_numbers_kept() -> None:
    assert tokenize("x 5 y 12") == ["5", "12"]


def test_term_frequencies() -> None:
    tf, length = term_frequencies("Apple apple banana. The apple!")
    assert tf == {"apple": 3, "banana": 1}
    assert length == 4
