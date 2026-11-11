"""Reciprocal Rank Fusion: combine ranked lists without comparing their raw scores.

    score(d) = Σ  weight(method) / (k + rank(d, method))      (rank starts at 1, k = 60)

A passage ranked well by both methods beats one ranked first by only one.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Fused:
    row: int
    score: float
    ranks: dict[str, int] = field(default_factory=dict)  # method -> 1-based rank


def rrf(
    rankings: dict[str, list[int]], weights: dict[str, float] | None = None, k: int = 60
) -> list[Fused]:
    """``rankings`` maps a method name to rows, best first. Ties keep the order of first
    appearance (method order, then rank) so results are deterministic."""
    weights = weights or {}
    fused: dict[int, Fused] = {}
    order: dict[int, int] = {}
    for method, rows in rankings.items():
        weight = weights.get(method, 1.0)
        for rank, row in enumerate(rows, start=1):
            item = fused.get(row)
            if item is None:
                item = fused[row] = Fused(row, 0.0)
                order[row] = len(order)
            item.score += weight / (k + rank)
            item.ranks[method] = rank
    return sorted(fused.values(), key=lambda f: (-f.score, order[f.row]))
