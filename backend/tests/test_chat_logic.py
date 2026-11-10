"""The prompt builder, citation checking, follow-up rewrite and preference detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from sidelamp.chat.citations import check_citations, is_refusal
from sidelamp.chat.preferences import PreferenceStore, detect_suggestion
from sidelamp.chat.prompts import (
    PREFERENCES_HEADER,
    SYSTEM_PROMPT,
    Source,
    answer_messages,
    fit_sources,
    looks_like_follow_up,
    rewrite_messages,
    source_label,
)
from sidelamp.chat.rewrite import clean_query, rewrite_follow_up
from sidelamp.errors import AppError, NotFound
from sidelamp.llm.base import LLMError
from sidelamp.llm.fake import FakeLLM


def src(n: int, text: str, **passage: object) -> Source:
    return Source(n, f"d{n}", f"doc{n}.pdf", {"text": text, "i": 0, **passage})


# ---------------------------------------------------------------- prompt builder


def test_system_prompt_has_every_rule() -> None:
    for phrase in (
        "ONLY the numbered sources",
        "Never use outside knowledge",
        "[1]",
        "exactly",
        "disagree",
        "rows you weren't shown",
        "Lead with the direct answer",
        "same language",
    ):
        assert phrase in SYSTEM_PROMPT


def test_sources_are_numbered_with_labels() -> None:
    sources = [src(1, "Refunds take 30 days.", page=2), src(2, "Ships in 5 days.", page=3)]
    system, user = answer_messages("How long do refunds take?", sources)
    assert system.role == "system" and user.role == "user"
    assert "[1] doc1.pdf, page 2\nRefunds take 30 days." in user.content
    assert "[2] doc2.pdf, page 3\nShips in 5 days." in user.content
    assert user.content.rstrip().endswith("How long do refunds take?")


def test_rewritten_query_is_added_as_context() -> None:
    _, user = answer_messages("and the south?", [src(1, "x")], standalone="sales in the south")
    assert "sales in the south" in user.content
    _, same = answer_messages("Sales?", [src(1, "x")], standalone="sales?")
    assert "the question means" not in same.content


def test_preferences_are_style_guidance_not_evidence() -> None:
    system, user = answer_messages("Q?", [src(1, "x")], preferences=["Answer in bullet points."])
    assert PREFERENCES_HEADER in system.content and "never be used as evidence" in system.content
    assert "- Answer in bullet points." in system.content
    assert "bullet points" not in user.content  # never mixed into the sources
    none_system, _ = answer_messages("Q?", [src(1, "x")], preferences=[])
    assert PREFERENCES_HEADER not in none_system.content


def test_labels() -> None:
    assert source_label("a.pdf", {"page": 3}) == "a.pdf, page 3"
    assert source_label("a.pdf", {"page": 3, "page_end": 4}) == "a.pdf, pages 3–4"
    assert source_label("b.csv", {"rows": [1, 20], "kind": "rows"}) == "b.csv, rows 1–20"
    assert source_label("c.epub", {"chapter": "One", "section": "Start"}) == "c.epub, One › Start"


def test_fit_sources_respects_budget_in_rank_order() -> None:
    long = " ".join(["word"] * 300)
    sources = [src(1, long), src(2, long), src(3, "short")]
    kept = fit_sources(sources, budget=500)
    assert [s.n for s in kept] == [1]
    tiny = fit_sources([src(1, long)], budget=100)  # the first one is always kept, shortened
    assert len(tiny) == 1 and tiny[0].passage["text"].endswith("…")
    assert len(tiny[0].passage["text"].split()) < 300


@pytest.mark.parametrize(
    ("question", "follow"),
    [
        ("and the south one?", True),
        ("What about 2023?", True),
        ("How long is the refund window for items bought online during the sale?", False),
    ],
)
def test_follow_up_detection(question: str, follow: bool) -> None:
    assert looks_like_follow_up(question) is follow


def test_rewrite_prompt_strips_citations_and_truncates() -> None:
    history = [("user", "Sales in the north?"), ("assistant", "120 units [1]. " + "x" * 600)]
    _, user = rewrite_messages(history, "and the south?")
    assert "[1]" not in user.content and "…" in user.content
    assert user.content.endswith("FOLLOW-UP: and the south?")


# ---------------------------------------------------------------- citations


def test_invalid_markers_are_removed_and_lists_normalised() -> None:
    check = check_citations("The window is 30 days [1, 9]. Items ship fast [7].", {1, 2})
    assert check.text == "The window is 30 days [1]. Items ship fast."
    assert check.cited == [1] and check.removed == 2
    assert "removed_citations" in check.flags


def test_answer_with_no_valid_citation_is_flagged() -> None:
    check = check_citations("The window is 30 days [5].", {1, 2})
    assert check.cited == [] and "no_citations" in check.flags


def test_plain_refusal_is_not_flagged_as_uncited() -> None:
    check = check_citations("Your documents don't cover this. Try searching for refunds.", {1})
    assert check.refusal and check.flags == ["refusal"]
    assert is_refusal("Les documents ne mentionnent pas ce point.")


def test_counts_uncited_sentences() -> None:
    check = check_citations(
        "Refunds take thirty days [1]. Shipping takes five business days here.", {1}
    )
    assert check.sentences == 2 and check.uncited_sentences == 1


def test_adjacent_markers_and_duplicates() -> None:
    check = check_citations("A fact stated here [2][2] and [1; 2].", {1, 2})
    assert check.text == "A fact stated here [2] and [1][2]."
    assert check.cited == [2, 1]


# ---------------------------------------------------------------- rewrite


@pytest.mark.anyio
async def test_rewrite_uses_the_model_and_cleans_its_reply() -> None:
    llm = FakeLLM(lambda m: 'Query: "sales in the south region"\nBecause the user…')
    history = [("user", "sales in the north region"), ("assistant", "120 [1].")]
    assert await rewrite_follow_up(llm, history, "and the south?") == "sales in the south region"
    sent = llm.calls[0]
    assert "standalone search query" in sent[0].content
    assert "User: sales in the north region" in sent[1].content


@pytest.mark.anyio
async def test_rewrite_falls_back_to_the_question() -> None:
    failing = FakeLLM(error=LLMError("down", "unreachable"))
    assert await rewrite_follow_up(failing, [("user", "a")], "and b?") == "and b?"
    assert clean_query("", "q") == "q"
    assert clean_query("x" * 500, "q") == "q"


# ---------------------------------------------------------------- preferences


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Remember that I prefer short answers", "I prefer short answers."),
        ("From now on, answer in French.", "Answer in French."),
        ("please keep in mind that I'm a nurse", "I'm a nurse."),
        ("Always use bullet points", "Always use bullet points."),
        ("What does the report say?", None),
        ("Remember that", None),
    ],
)
def test_detect_suggestion(message: str, expected: str | None) -> None:
    assert detect_suggestion(message) == expected


def test_preference_store(tmp_path: Path) -> None:
    store = PreferenceStore(tmp_path / "preferences.json")
    suggestion = store.suggest("Answer in French.", "chat1")
    assert store.active() == []  # a suggestion isn't used until it's saved
    saved = store.accept(suggestion.id)
    assert store.active() == ["Answer in French."]
    store.edit(saved.id, "  Answer   in English. ")
    assert store.active() == ["Answer in English."]
    store.set_enabled(False)
    assert store.active() == []  # switched off: nothing goes into the prompt
    store.set_enabled(True)
    other = store.add("Keep it short.")
    store.delete(other.id)
    assert store.active() == ["Answer in English."]
    assert store.clear() == 1 and store.active() == []
    with pytest.raises(NotFound):
        store.delete("nope")
    with pytest.raises(AppError):
        store.add("   ")
    dismissed = store.suggest("Use tables.")
    store.dismiss(dismissed.id)
    assert store.load().suggestions == []


def test_broken_preferences_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text("{not json", encoding="utf-8")
    store = PreferenceStore(path)
    assert store.active() == []
    store.add("Be brief.")  # and saving again repairs it
    assert store.active() == ["Be brief."]
