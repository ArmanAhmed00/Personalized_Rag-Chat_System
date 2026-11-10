"""Rerankers look at the query and each candidate passage together and re-order the top results.

* ``model``: a fastembed cross-encoder on the CPU (default ``Xenova/ms-marco-MiniLM-L-6-v2``).
* ``lexical``: a small, dependency-free scorer: how much of the query appears in the passage,
  with a bonus for query words that appear next to each other.
* ``off``: keep the fused order.

If the model can't be loaded, search keeps the fused order and says why.

To add a reranker: a class with ``name`` and ``score(query, texts) -> list[float]``.
"""

from __future__ import annotations

import itertools
import math
import threading
from pathlib import Path
from typing import Protocol

from sidelamp.search.tokenizer import tokenize


class RerankUnavailable(Exception):
    """The reranker can't run; the message says why in plain words."""


class Reranker(Protocol):
    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


class LexicalReranker:
    name = "lexical"

    def score(self, query: str, texts: list[str]) -> list[float]:
        q_terms = list(dict.fromkeys(tokenize(query)))
        if not q_terms:
            return [0.0] * len(texts)
        q_pairs = set(itertools.pairwise(tokenize(query)))
        scores = []
        for text in texts:
            tokens = tokenize(text)
            present = set(tokens)
            coverage = sum(1 for t in q_terms if t in present) / len(q_terms)
            pairs = set(itertools.pairwise(tokens))
            adjacency = len(q_pairs & pairs) / len(q_pairs) if q_pairs else 0.0
            # Mild preference for focused passages over very long ones with the same words.
            focus = 1.0 / (1.0 + math.log1p(max(0, len(tokens) - 50) / 50))
            scores.append(coverage + 0.5 * adjacency + 0.1 * focus)
        return scores


class CrossEncoderReranker:
    def __init__(self, model: str, cache_dir: Path) -> None:
        self.model = model
        self.name = f"model:{model}"
        self.cache_dir = cache_dir
        self._engine: object | None = None
        self._error: str | None = None
        self._lock = threading.Lock()

    def _load(self) -> object:
        with self._lock:
            if self._engine is not None:
                return self._engine
            if self._error is not None:
                raise RerankUnavailable(self._error)
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._engine = TextCrossEncoder(
                    model_name=self.model, cache_dir=str(self.cache_dir)
                )
            except Exception:
                self._error = (
                    f"The reranker model “{self.model}” couldn't be loaded (the first "
                    f"use needs an internet connection to download it), so results "
                    f"are in their combined order."
                )
                raise RerankUnavailable(self._error) from None
            return self._engine

    def score(self, query: str, texts: list[str]) -> list[float]:
        engine = self._load()
        return [float(s) for s in engine.rerank(query, texts)]  # type: ignore[attr-defined]


def make_reranker(mode: str, model: str, cache_dir: Path) -> Reranker | None:
    if mode == "off":
        return None
    if mode == "lexical":
        return LexicalReranker()
    return CrossEncoderReranker(model, cache_dir)
