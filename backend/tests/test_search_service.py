"""Hybrid search: modes, reranking and its fallback, explanations, selection."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from sidelamp.ingest.embed import FakeEmbedder
from sidelamp.search.index import SearchIndex
from sidelamp.search.rerank import CrossEncoderReranker, LexicalReranker, Reranker
from sidelamp.search.service import SearchOptions, SearchService
from sidelamp.search.tokenizer import term_frequencies

TEXTS = [
    "Shipping takes five to seven business days within the country.",
    "Refunds are issued within 30 days of delivery when the receipt is kept.",
    "Our refund policy excludes items bought on sale; those can be exchanged.",
    "The warehouse in Lyon handles all returns for Europe.",
    "Gift cards never expire and can be used online or in store.",
]


def build(texts: list[str] = TEXTS, doc: str = "d") -> tuple[SearchIndex, FakeEmbedder]:
    embedder = FakeEmbedder()
    index = SearchIndex()
    passages = []
    for i, text in enumerate(texts):
        tf, length = term_frequencies(text)
        passages.append({"i": i, "text": text, "tf": tf, "len": length})
    index.add(doc, passages, embedder.embed_passages(texts), embedder.model_id)
    return index, embedder


def service(
    index: SearchIndex, embedder: FakeEmbedder | None, reranker: Reranker | None = None
) -> SearchService:
    return SearchService(index, lambda: embedder, lambda: reranker, lambda: SearchOptions())


def test_best_mode_combines_both_methods() -> None:
    index, embedder = build()
    result = service(index, embedder).search("refund policy for sale items", limit=3)
    assert result.hits[0].passage["i"] == 2
    top = result.hits[0]
    assert set(top.ranks) == {"vector", "keyword"}
    assert "refund" in top.matched and "policy" in top.matched
    assert "Found by exact words (#1) and meaning" in top.explanation()
    assert set(result.timings) >= {"embed_ms", "vector_ms", "keyword_ms", "total_ms"}


def test_words_and_meaning_modes() -> None:
    index, embedder = build()
    svc = service(index, embedder)
    words = svc.search("Lyon", mode="words")
    assert [h.passage["i"] for h in words.hits] == [3]
    assert set(words.hits[0].ranks) == {"keyword"}
    meaning = svc.search("warehouse returns Europe", mode="meaning")
    assert set(meaning.hits[0].ranks) == {"vector"}
    assert meaning.hits[0].passage["i"] == 3


def test_reranker_moves_results_and_says_so() -> None:
    index, embedder = build()

    class Prefers4(LexicalReranker):
        def score(self, query: str, texts: list[str]) -> list[float]:
            return [10.0 if "Gift" in t else 0.0 for t in texts]

    result = service(index, embedder, Prefers4()).search("refund days", limit=5)
    assert result.reranked
    first = result.hits[0]
    assert first.passage["i"] == 4
    assert first.moved_from is not None and first.moved_from > 1
    assert f"The reranker moved this up from #{first.moved_from}." in first.explanation()


def test_rerank_can_be_skipped_per_request() -> None:
    index, embedder = build()
    svc = service(index, embedder, LexicalReranker())
    result = svc.search("refund", rerank=False)
    assert not result.reranked
    assert all(h.moved_from is None for h in result.hits)


def test_model_that_cannot_load_keeps_fused_order(tmp_path: Path) -> None:
    index, embedder = build()
    broken = CrossEncoderReranker("no-such/model-anywhere", tmp_path)
    fused = service(index, embedder).search("refund policy", rerank=False)
    result = service(index, embedder, broken).search("refund policy")
    assert not result.reranked
    assert [h.row for h in result.hits] == [h.row for h in fused.hits]
    assert any("reranker model" in note for note in result.notes)


def test_meaning_search_failure_falls_back_to_words() -> None:
    index, _ = build()

    class Broken(FakeEmbedder):
        def embed_query(self, text: str) -> np.ndarray:
            raise RuntimeError("model download failed")

    result = service(index, Broken()).search("Lyon")
    assert [h.passage["i"] for h in result.hits] == [3]
    assert any("meaning isn't available" in n for n in result.notes)


def test_selection_mask_and_empty_selection() -> None:
    index, embedder = build()
    other_texts = ["Refund rules for the Lyon office."]
    tf, length = term_frequencies(other_texts[0])
    index.add(
        "other",
        [{"i": 0, "text": other_texts[0], "tf": tf, "len": length}],
        embedder.embed_passages(other_texts),
        embedder.model_id,
    )
    svc = service(index, embedder)
    only = svc.search("Lyon refund", doc_ids=["other"])
    assert {h.doc_id for h in only.hits} == {"other"}
    nothing = svc.search("Lyon", doc_ids=[])
    assert nothing.hits == [] and nothing.notes


def test_stop_word_only_query_explains() -> None:
    index, embedder = build()
    result = service(index, embedder).search("what is the", mode="words")
    assert result.hits == []
    assert any("very common words" in n for n in result.notes)


def test_lexical_reranker_prefers_coverage_and_adjacency() -> None:
    scores = LexicalReranker().score(
        "refund policy", ["policy about refund", "refund policy details", "shipping only"]
    )
    assert scores[1] > scores[0] > scores[2]
