"""Okapi BM25 scoring.

For a query term *t* in passage *d*::

    idf(t)     = ln(1 + (N - df + 0.5) / (df + 0.5))
    score(t,d) = idf(t) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(d) / avg_len))

with ``k1 = 1.2`` and ``b = 0.75``. This is the Okapi formula with the "+1 inside the log" idf
used by Lucene, so a term that appears in most passages still scores a little instead of going
negative. A passage's score is the sum over the distinct query terms it contains.
"""

from __future__ import annotations

import math

import numpy as np

K1 = 1.2
B = 0.75


def idf(total: int, df: int) -> float:
    return math.log(1.0 + (total - df + 0.5) / (df + 0.5))


def term_score(
    tf: float, length: float, avg_length: float, term_idf: float, k1: float = K1, b: float = B
) -> float:
    norm = k1 * (1.0 - b + b * (length / avg_length if avg_length > 0 else 0.0))
    return term_idf * tf * (k1 + 1.0) / (tf + norm)


def term_scores(
    tfs: np.ndarray,
    lengths: np.ndarray,
    avg_length: float,
    term_idf: float,
    k1: float = K1,
    b: float = B,
) -> np.ndarray:
    """Vectorised :func:`term_score` for all passages that contain the term."""
    ratio = lengths / avg_length if avg_length > 0 else np.zeros_like(lengths)
    norm = k1 * (1.0 - b + b * ratio)
    result: np.ndarray = term_idf * tfs * (k1 + 1.0) / (tfs + norm)
    return result
