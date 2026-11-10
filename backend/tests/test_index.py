"""The in-memory index: BM25 against hand-computed scores, vectors, masks, incremental updates,
fusion, and the 20k-passage speed target."""

from __future__ import annotations

import time

import numpy as np
import pytest

from sidelamp.search.bm25 import idf
from sidelamp.search.fusion import rrf
from sidelamp.search.index import SearchIndex


def passage(i: int, tf: dict[str, int], text: str = "") -> dict[str, object]:
    return {"i": i, "text": text or " ".join(tf), "tf": tf, "len": sum(tf.values())}


def unit(*values: float) -> np.ndarray:
    v = np.asarray(values, dtype=np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def fruit() -> SearchIndex:
    """Three passages: N = 3, average length 7/3."""
    index = SearchIndex()
    index.add(
        "d",
        [
            passage(0, {"apple": 2, "banana": 1}),
            passage(1, {"apple": 1}),
            passage(2, {"cherry": 3}),
        ],
        np.vstack([unit(1, 0), unit(0.8, 0.6), unit(0, 1)]),
        "m",
    )
    return index


# Hand-computed with k1 = 1.2, b = 0.75 (see bm25.py for the formula):
#   idf(apple)  = ln(1 + (3 - 2 + 0.5) / (2 + 0.5)) = ln(1.6)   = 0.470003629
#   idf(banana) = ln(1 + (3 - 1 + 0.5) / (1 + 0.5)) = ln(8/3)   = 0.980829253
#   p0, apple: 0.470003629 * 2 * 2.2 / (2 + 1.2 * (0.25 + 0.75 * 3 / (7/3))) = 0.598186437
#   p1, apple: 0.470003629 * 1 * 2.2 / (1 + 1.2 * (0.25 + 0.75 * 1 / (7/3))) = 0.613394567
#   p0, banana adds 0.878184331, total 1.476370768
def test_bm25_matches_hand_computed_scores(fruit: SearchIndex) -> None:
    assert idf(3, 2) == pytest.approx(0.470003629)
    results = fruit.keyword_search(["apple"], 10)
    assert [(row, round(score, 6)) for row, score, _ in results] == [(1, 0.613395), (0, 0.598186)]
    both = fruit.keyword_search(["apple", "banana"], 10)
    assert both[0][0] == 0 and both[0][1] == pytest.approx(1.476370768)
    assert both[0][2] == ["apple", "banana"]


def test_bm25_ignores_unknown_terms_and_repeats(fruit: SearchIndex) -> None:
    assert fruit.keyword_search(["durian"], 10) == []
    once = fruit.keyword_search(["apple"], 10)
    twice = fruit.keyword_search(["apple", "apple"], 10)
    assert once == twice


def test_vector_search_is_cosine(fruit: SearchIndex) -> None:
    results = fruit.vector_search(unit(1, 0.1), 3)
    assert [row for row, _ in results] == [0, 1, 2]
    assert results[0][1] == pytest.approx(float(unit(1, 0) @ unit(1, 0.1)), abs=1e-6)


def test_mask_restricts_documents() -> None:
    index = SearchIndex()
    index.add("a", [passage(0, {"x": 1})], unit(1, 0).reshape(1, -1), "m")
    index.add("b", [passage(0, {"x": 1})], unit(1, 0).reshape(1, -1), "m")
    rows = [r for r, _ in index.vector_search(unit(1, 0), 5, index.mask(["b"]))]
    assert [index.lookup(r)[0] for r in rows] == ["b"]
    assert index.keyword_search(["x"], 5, index.mask(["missing"])) == []


def test_incremental_add_replace_remove() -> None:
    index = SearchIndex()
    index.add("a", [passage(0, {"old": 1})], unit(1, 0).reshape(1, -1), "m")
    index.add("b", [passage(0, {"other": 1})], unit(0, 1).reshape(1, -1), "m")
    index.add(
        "a",
        [passage(0, {"new": 1}), passage(1, {"new": 2})],
        np.vstack([unit(1, 0), unit(1, 1)]),
        "m",
    )  # replaced
    assert index.keyword_search(["old"], 5) == []
    assert {index.lookup(r)[0] for r, _, _ in index.keyword_search(["new"], 5)} == {"a"}
    assert index.passage_count == 3
    index.remove("b")
    assert index.keyword_search(["other"], 5) == []
    assert index.document_count == 1 and index.passage_count == 2


def test_compaction_keeps_results_consistent() -> None:
    index = SearchIndex()
    rng = np.random.default_rng(1)
    for d in range(10):
        vecs = rng.normal(size=(5, 8)).astype(np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        index.add(f"d{d}", [passage(i, {f"t{d}": 1, "common": 1}) for i in range(5)], vecs, "m")
    for d in range(7):
        index.remove(f"d{d}")  # triggers compaction
    assert index.passage_count == 15
    keyword_docs = {index.lookup(r)[0] for r, _, _ in index.keyword_search(["common"], 50)}
    assert keyword_docs == {"d7", "d8", "d9"}
    assert index.keyword_search(["t8"], 10)[0][2] == ["t8"]
    vector_docs = {index.lookup(r)[0] for r, _ in index.vector_search(rng.normal(size=8), 50)}
    assert vector_docs == {"d7", "d8", "d9"}


def test_vectors_from_another_model_are_refused() -> None:
    index = SearchIndex()
    assert index.add("a", [passage(0, {"x": 1})], unit(1, 0).reshape(1, -1), "model-a")
    assert not index.add("b", [passage(0, {"x": 1})], unit(1, 0).reshape(1, -1), "model-b")
    assert not index.add("c", [passage(0, {"x": 1})], unit(1, 0, 0).reshape(1, -1), "model-a")
    assert index.document_count == 1


def test_rrf_formula_and_weights() -> None:
    fused = rrf({"vector": [10, 20, 30], "keyword": [20, 40]}, k=60)
    by_row = {f.row: f for f in fused}
    assert by_row[20].score == pytest.approx(1 / 62 + 1 / 61)
    assert by_row[10].score == pytest.approx(1 / 61)
    assert [f.row for f in fused] == [20, 10, 40, 30]
    assert by_row[20].ranks == {"vector": 2, "keyword": 1}
    weighted = rrf({"vector": [1], "keyword": [2]}, {"vector": 1.0, "keyword": 2.0})
    assert [f.row for f in weighted] == [2, 1]


def test_rrf_ties_are_deterministic() -> None:
    fused = rrf({"vector": [1, 2], "keyword": [2, 1]})
    assert [f.row for f in fused] == [1, 2]


def test_speed_target_for_20k_passages() -> None:
    """Target: ~30 ms for vectors and ~25 ms for BM25 at 20k passages. Asserted loosely so
    slow CI machines don't fail; the timings are printed for the record."""
    rng = np.random.default_rng(7)
    index = SearchIndex()
    vocab = [f"w{i}" for i in range(20_000)]
    zipf = rng.zipf(1.3, size=(20_000, 60)) % len(vocab)
    for d in range(40):
        rows = range(d * 500, (d + 1) * 500)
        passages = []
        for r in rows:
            words, counts = np.unique(zipf[r], return_counts=True)
            passages.append(
                passage(r, {vocab[w]: int(c) for w, c in zip(words, counts, strict=True)})
            )
        vecs = rng.normal(size=(500, 384)).astype(np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        index.add(f"d{d}", passages, vecs, "m")
    assert index.passage_count == 20_000
    query = rng.normal(size=384).astype(np.float32)
    terms = ["w1", "w5", "w40", "w300", "w2000"]
    index.keyword_search(terms, 40)  # warm the posting caches, as repeated queries would
    vector_ms = _median_ms(lambda: index.vector_search(query, 40))
    keyword_ms = _median_ms(lambda: index.keyword_search(terms, 40))
    print(f"\n20k passages: vectors {vector_ms:.1f} ms, BM25 {keyword_ms:.1f} ms")
    assert vector_ms < 120
    assert keyword_ms < 100


def _median_ms(fn: object, runs: int = 7) -> float:
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()  # type: ignore[operator]
        times.append((time.perf_counter() - start) * 1000)
    return sorted(times)[len(times) // 2]
