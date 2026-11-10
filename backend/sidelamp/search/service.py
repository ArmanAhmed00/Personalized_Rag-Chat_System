"""Hybrid search::

    question ─┬─ vector search (top 40) ─┐
              └─ BM25 keywords  (top 40) ─┴─ RRF ─▶ cross-encoder rerank (top 30) ─▶ top k

Modes: ``meaning`` (vectors only), ``words`` (keywords only) and ``best`` (both, fused; the
default). The rerank step can be skipped per request. Every hit explains itself: its rank in each
method, the words that matched, and how the reranker moved it ("moved up from #4").
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np

from sidelamp.ingest.embed import Embedder
from sidelamp.search.fusion import rrf
from sidelamp.search.index import SearchIndex
from sidelamp.search.rerank import Reranker, RerankUnavailable
from sidelamp.search.tokenizer import tokenize

Mode = Literal["meaning", "words", "best"]
METHOD_LABELS = {"vector": "meaning", "keyword": "exact words"}


@dataclass
class SearchOptions:
    vector_k: int = 40
    keyword_k: int = 40
    rerank_k: int = 30
    rrf_k: int = 60
    vector_weight: float = 1.0
    keyword_weight: float = 1.0


@dataclass
class Hit:
    row: int
    doc_id: str
    passage: dict[str, Any]
    score: float
    rank: int
    ranks: dict[str, int] = field(default_factory=dict)
    fused_rank: int | None = None
    matched: list[str] = field(default_factory=list)
    rerank_score: float | None = None

    @property
    def moved_from(self) -> int | None:
        if self.fused_rank is not None and self.fused_rank != self.rank:
            return self.fused_rank
        return None

    def explanation(self) -> str:
        parts = []
        found = [f"{METHOD_LABELS[m]} (#{r})" for m, r in sorted(self.ranks.items())]
        if found:
            parts.append("Found by " + " and ".join(found) + ".")
        if self.matched:
            parts.append("Matched words: " + ", ".join(self.matched) + ".")
        moved = self.moved_from
        if moved is not None:
            direction = "up" if moved > self.rank else "down"
            parts.append(f"The reranker moved this {direction} from #{moved}.")
        return " ".join(parts)


@dataclass
class SearchResult:
    query: str
    mode: Mode
    hits: list[Hit]
    notes: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    reranked: bool = False
    searched_passages: int = 0


class SearchService:
    def __init__(
        self,
        index: SearchIndex,
        embedder: Callable[[], Embedder | None],
        reranker: Callable[[], Reranker | None],
        options: Callable[[], SearchOptions],
    ) -> None:
        self.index = index
        self.embedder = embedder
        self.reranker = reranker
        self.options = options

    def search(
        self,
        query: str,
        *,
        mode: Mode = "best",
        limit: int = 10,
        doc_ids: Iterable[str] | None = None,
        rerank: bool = True,
    ) -> SearchResult:
        opts = self.options()
        started = time.perf_counter()
        result = SearchResult(query=query, mode=mode, hits=[])
        selection = list(doc_ids) if doc_ids is not None else None
        mask = self.index.mask(selection)
        result.searched_passages = int(mask.sum())
        if not query.strip() or not result.searched_passages:
            if not result.searched_passages:
                result.notes.append(
                    "There's nothing to search yet: no documents are ready"
                    + (" in this selection." if selection is not None else ".")
                )
            return result

        rankings: dict[str, list[int]] = {}
        matched: dict[int, list[str]] = {}
        if mode in ("meaning", "best"):
            vector_rows = self._vector(
                query, opts.vector_k if mode == "best" else max(opts.vector_k, limit), mask, result
            )
            if vector_rows is not None:
                rankings["vector"] = vector_rows
        if mode in ("words", "best"):
            t = time.perf_counter()
            terms = tokenize(query)
            keyword = self.index.keyword_search(
                terms, opts.keyword_k if mode == "best" else max(opts.keyword_k, limit), mask
            )
            result.timings["keyword_ms"] = _ms(t)
            rankings["keyword"] = [row for row, _, _ in keyword]
            matched = {row: words for row, _, words in keyword}
            if not terms:
                result.notes.append(
                    "The question has only very common words, so exact-word "
                    "search found nothing to match."
                )

        t = time.perf_counter()
        fused = rrf(
            rankings, {"vector": opts.vector_weight, "keyword": opts.keyword_weight}, k=opts.rrf_k
        )
        result.timings["fusion_ms"] = _ms(t)
        hits: list[Hit] = []
        for position, item in enumerate(fused, start=1):
            doc_id, passage = self.index.lookup(item.row)
            hits.append(
                Hit(
                    row=item.row,
                    doc_id=doc_id,
                    passage=passage,
                    score=item.score,
                    rank=position,
                    ranks=item.ranks,
                    fused_rank=position,
                    matched=matched.get(item.row, []),
                )
            )

        if rerank and hits:
            hits = self._rerank(query, hits, opts.rerank_k, result)
        for position, hit in enumerate(hits, start=1):
            hit.rank = position
            if not result.reranked:
                hit.fused_rank = None
        result.hits = hits[:limit]
        result.timings["total_ms"] = _ms(started)
        return result

    def _vector(
        self, query: str, k: int, mask: np.ndarray, result: SearchResult
    ) -> list[int] | None:
        embedder = self.embedder()
        if embedder is None:
            result.notes.append("Search by meaning isn't available right now.")
            return None
        t = time.perf_counter()
        try:
            vector = embedder.embed_query(query)
        except Exception as exc:
            reason = str(exc) or "the search model couldn't be loaded"
            result.notes.append(f"Search by meaning isn't available: {reason}")
            return None
        result.timings["embed_ms"] = _ms(t)
        if self.index.model_id not in (None, embedder.model_id):
            result.notes.append(
                "Documents are being prepared for the new search model; "
                "meaning search will be complete once that finishes."
            )
        t = time.perf_counter()
        rows = [row for row, _ in self.index.vector_search(vector, k, mask)]
        result.timings["vector_ms"] = _ms(t)
        return rows

    def _rerank(self, query: str, hits: list[Hit], top: int, result: SearchResult) -> list[Hit]:
        reranker = self.reranker()
        if reranker is None:
            return hits
        head, tail = hits[:top], hits[top:]
        t = time.perf_counter()
        try:
            scores = reranker.score(query, [str(h.passage.get("text", "")) for h in head])
        except RerankUnavailable as exc:
            result.notes.append(str(exc))
            return hits
        except Exception:
            result.notes.append("The reranker failed, so results are in their combined order.")
            return hits
        result.timings["rerank_ms"] = _ms(t)
        for hit, score in zip(head, scores, strict=True):
            hit.rerank_score = score
        head.sort(key=lambda h: (-(h.rerank_score or 0.0), h.fused_rank or 0))
        result.reranked = True
        return head + tail


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def hit_dict(hit: Hit) -> dict[str, Any]:
    data = asdict(hit)
    data.pop("passage")
    data["moved_from"] = hit.moved_from
    data["explanation"] = hit.explanation()
    return data
