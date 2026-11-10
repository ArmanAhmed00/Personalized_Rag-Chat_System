"""The in-memory search index. The files on disk are the source of truth; this is rebuilt from
them at startup and kept up to date as documents are added, changed and deleted.

* **Vectors:** one float32, L2-normalised matrix, so cosine similarity for every passage is a
  single matrix–vector product.
* **Keywords:** an inverted index ``term → {row: term frequency}`` with each row's length, for
  BM25.
* **Updates are incremental.** Adding a document appends rows; deleting one marks its rows dead
  and removes its postings. Dead rows are compacted away once they're a third of the matrix.
* **Document selection** is a boolean mask applied before ranking.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np

from sidelamp.search.bm25 import idf, term_scores

_GROW = 1.5
_MIN_CAPACITY = 1024


@dataclass
class DocEntry:
    doc_id: str
    rows: np.ndarray  # int64 row numbers, in passage order
    passages: list[dict[str, Any]]  # term frequencies dropped to save memory
    terms: set[str]  # every term that occurs in the document, for fast removal


class SearchIndex:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.clear()

    # ---------------------------------------------------------------- building

    def clear(self) -> None:
        with self._lock:
            self.dim: int | None = None
            self.model_id: str | None = None
            self._vectors = np.zeros((0, 0), dtype=np.float32)
            self._alive = np.zeros(0, dtype=bool)
            self._doc_of = np.zeros(0, dtype=np.int32)  # row -> doc slot
            self._passage_of = np.zeros(0, dtype=np.int32)  # row -> passage index
            self._lengths = np.zeros(0, dtype=np.float32)
            self._n = 0
            self._docs: dict[str, DocEntry] = {}
            self._slots: list[str | None] = []
            self._slot_of: dict[str, int] = {}
            self._postings: dict[str, dict[int, int]] = {}
            self._arrays: dict[str, tuple[np.ndarray, np.ndarray]] = {}
            self._total_length = 0.0
            self._alive_count = 0
            self.version = 0

    def _ensure_capacity(self, extra: int, dim: int) -> None:
        needed = self._n + extra
        capacity = self._vectors.shape[0]
        if needed <= capacity and self._vectors.shape[1] == dim:
            return
        new_capacity = max(_MIN_CAPACITY, int(max(needed, capacity) * _GROW))
        vectors = np.zeros((new_capacity, dim), dtype=np.float32)
        if self._n:
            vectors[: self._n] = self._vectors[: self._n]
        self._vectors = vectors
        for name, dtype in (
            ("_alive", bool),
            ("_doc_of", np.int32),
            ("_passage_of", np.int32),
            ("_lengths", np.float32),
        ):
            old = getattr(self, name)
            grown = np.zeros(new_capacity, dtype=dtype)
            grown[: self._n] = old[: self._n]
            setattr(self, name, grown)

    def add(
        self,
        doc_id: str,
        passages: list[dict[str, Any]],
        vectors: np.ndarray,
        model_id: str | None = None,
    ) -> bool:
        """Add or replace a document. Returns False (and changes nothing) if its vectors come
        from a different model than the rest of the index."""
        vectors = np.asarray(vectors, dtype=np.float32)
        if len(passages) != len(vectors):
            raise ValueError("passages and vectors differ in length")
        with self._lock:
            if model_id is not None and self.model_id not in (None, model_id):
                return False
            if len(passages) and self.dim not in (None, vectors.shape[1]):
                return False
            self.remove(doc_id)
            if not passages:
                return True
            dim = vectors.shape[1]
            self.dim = dim
            if model_id is not None:
                self.model_id = model_id
            self._ensure_capacity(len(passages), dim)
            slot = self._slot_of.get(doc_id)
            if slot is None:
                slot = len(self._slots)
                self._slots.append(doc_id)
                self._slot_of[doc_id] = slot
            start = self._n
            rows = np.arange(start, start + len(passages), dtype=np.int64)
            self._vectors[start : start + len(passages)] = vectors
            self._alive[rows] = True
            self._doc_of[rows] = slot
            self._passage_of[rows] = np.arange(len(passages), dtype=np.int32)
            kept: list[dict[str, Any]] = []
            terms: set[str] = set()
            for row, passage in zip(rows.tolist(), passages, strict=True):
                tf: dict[str, int] = passage.get("tf") or {}
                length = float(passage.get("len") or sum(tf.values()))
                self._lengths[row] = length
                self._total_length += length
                for term, count in tf.items():
                    self._postings.setdefault(term, {})[row] = int(count)
                    self._arrays.pop(term, None)
                    terms.add(term)
                kept.append({k: v for k, v in passage.items() if k != "tf"})
            self._n += len(passages)
            self._alive_count += len(passages)
            self._docs[doc_id] = DocEntry(doc_id, rows, kept, terms)
            self.version += 1
            return True

    def remove(self, doc_id: str) -> bool:
        with self._lock:
            entry = self._docs.pop(doc_id, None)
            if entry is None:
                return False
            row_set = set(entry.rows.tolist())
            for term in entry.terms:  # only terms that occur in this document are touched
                rows = self._postings.get(term)
                if rows is None:
                    continue
                for row in row_set.intersection(rows):
                    del rows[row]
                self._arrays.pop(term, None)
                if not rows:
                    del self._postings[term]
            self._alive[entry.rows] = False
            self._total_length -= float(self._lengths[entry.rows].sum())
            self._alive_count -= len(entry.rows)
            slot = self._slot_of.pop(doc_id)
            self._slots[slot] = None
            self.version += 1
            if self._n and (self._n - self._alive_count) > self._n / 3:
                self._compact()
            if not self._docs:
                self.clear()
            return True

    def _compact(self) -> None:
        keep = np.flatnonzero(self._alive[: self._n])
        remap = np.full(self._n, -1, dtype=np.int64)
        remap[keep] = np.arange(len(keep))
        self._vectors[: len(keep)] = self._vectors[keep]
        self._alive[: len(keep)] = True
        self._alive[len(keep) : self._n] = False
        self._doc_of[: len(keep)] = self._doc_of[keep]
        self._passage_of[: len(keep)] = self._passage_of[keep]
        self._lengths[: len(keep)] = self._lengths[keep]
        self._n = len(keep)
        for entry in self._docs.values():
            entry.rows = remap[entry.rows]
        self._postings = {
            term: {int(remap[r]): tf for r, tf in rows.items()}
            for term, rows in self._postings.items()
        }
        self._arrays.clear()

    # ---------------------------------------------------------------- reading

    @property
    def passage_count(self) -> int:
        return self._alive_count

    @property
    def document_count(self) -> int:
        return len(self._docs)

    def has(self, doc_id: str) -> bool:
        return doc_id in self._docs

    def doc_ids(self) -> list[str]:
        with self._lock:
            return list(self._docs)

    def mask(self, doc_ids: Iterable[str] | None) -> np.ndarray:
        """Rows that may be returned: alive, and in the selected documents (if any)."""
        with self._lock:
            mask = self._alive[: self._n].copy()
            if doc_ids is None:
                return mask
            selected = np.zeros(len(self._slots), dtype=bool)
            for doc_id in doc_ids:
                slot = self._slot_of.get(doc_id)
                if slot is not None:
                    selected[slot] = True
            if not selected.any():
                return np.zeros(self._n, dtype=bool)
            return mask & selected[self._doc_of[: self._n]]

    def lookup(self, row: int) -> tuple[str, dict[str, Any]]:
        with self._lock:
            slot = int(self._doc_of[row])
            doc_id = self._slots[slot]
            if doc_id is None:
                raise KeyError(row)
            return doc_id, self._docs[doc_id].passages[int(self._passage_of[row])]

    def vector_search(
        self, query: np.ndarray, k: int, mask: np.ndarray | None = None
    ) -> list[tuple[int, float]]:
        with self._lock:
            if not self._n or self.dim is None:
                return []
            q = np.asarray(query, dtype=np.float32).reshape(-1)
            if q.shape[0] != self.dim:
                return []
            scores = self._vectors[: self._n] @ q
            allowed = mask if mask is not None else self._alive[: self._n]
            return _top(scores, allowed, k)

    def _posting_arrays(self, term: str) -> tuple[np.ndarray, np.ndarray] | None:
        cached = self._arrays.get(term)
        if cached is not None:
            return cached
        rows = self._postings.get(term)
        if not rows:
            return None
        arrays = (
            np.fromiter(rows.keys(), dtype=np.int64, count=len(rows)),
            np.fromiter(rows.values(), dtype=np.float32, count=len(rows)),
        )
        self._arrays[term] = arrays
        return arrays

    def keyword_search(
        self, terms: list[str], k: int, mask: np.ndarray | None = None
    ) -> list[tuple[int, float, list[str]]]:
        """BM25 over the distinct query terms. Returns ``(row, score, matched terms)``."""
        with self._lock:
            if not self._n or not terms or not self._alive_count:
                return []
            unique = list(dict.fromkeys(terms))
            total = self._alive_count
            avg = self._total_length / total if total else 0.0
            scores = np.zeros(self._n, dtype=np.float64)
            found: list[str] = []
            for term in unique:
                arrays = self._posting_arrays(term)
                if arrays is None:
                    continue
                rows, tfs = arrays
                found.append(term)
                term_idf = idf(total, len(rows))
                np.add.at(scores, rows, term_scores(tfs, self._lengths[rows], avg, term_idf))
            if not found:
                return []
            allowed = (mask if mask is not None else self._alive[: self._n]) & (scores > 0)
            top = _top(scores, allowed, k)
            return [
                (row, score, [t for t in found if row in self._postings[t]]) for row, score in top
            ]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "documents": len(self._docs),
                "passages": self._alive_count,
                "terms": len(self._postings),
                "dim": self.dim,
                "model": self.model_id,
                "vector_mb": round(self._vectors.nbytes / 1_048_576, 1),
            }


def _top(scores: np.ndarray, allowed: np.ndarray, k: int) -> list[tuple[int, float]]:
    candidates = np.flatnonzero(allowed)
    if not len(candidates) or k <= 0:
        return []
    values = scores[candidates]
    if len(candidates) > k:
        part = np.argpartition(-values, k - 1)[:k]
        candidates, values = candidates[part], values[part]
    order = np.lexsort((candidates, -values))  # score desc, then row asc for ties
    return [(int(candidates[i]), float(values[i])) for i in order]
