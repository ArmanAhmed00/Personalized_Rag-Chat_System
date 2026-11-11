"""Two CSVs side by side: what changed between them.

Columns added or removed, type changes, for shared number columns the mean shift and
standardised difference (Cohen's d), for shared category columns new and vanished categories, and
changes in empty cells.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from outlawz.analysis.run import jsonable
from outlawz.tabular import ColumnKind, fmt_number

MAX_CATEGORIES = 20


def _cohen_d(a: pd.Series, b: pd.Series) -> float | None:
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    pooled = ((n1 - 1) * a.var(ddof=1) + (n2 - 1) * b.var(ddof=1)) / (n1 + n2 - 2)
    if not pooled or math.isnan(pooled):
        return None
    return float((b.mean() - a.mean()) / math.sqrt(pooled))


def _size(d: float | None) -> str:
    if d is None:
        return "can't be measured"
    size = abs(d)
    if size < 0.2:
        return "negligible"
    if size < 0.5:
        return "small"
    if size < 0.8:
        return "medium"
    return "large"


def compare_frames(
    a: pd.DataFrame,
    kinds_a: dict[str, ColumnKind],
    b: pd.DataFrame,
    kinds_b: dict[str, ColumnKind],
    *,
    name_a: str = "A",
    name_b: str = "B",
) -> dict[str, Any]:
    cols_a, cols_b = list(a.columns), list(b.columns)
    added = [c for c in cols_b if c not in kinds_a]
    removed = [c for c in cols_a if c not in kinds_b]
    shared = [c for c in cols_a if c in kinds_b]
    type_changes = [
        {"column": c, "from": kinds_a[c], "to": kinds_b[c]}
        for c in shared
        if kinds_a[c] != kinds_b[c]
    ]
    numbers: list[dict[str, Any]] = []
    categories: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for c in shared:
        empty_a, empty_b = int(a[c].isna().sum()), int(b[c].isna().sum())
        share_a = empty_a / len(a) if len(a) else 0.0
        share_b = empty_b / len(b) if len(b) else 0.0
        if empty_a != empty_b:
            missing.append(
                {
                    "column": c,
                    "empty_a": empty_a,
                    "empty_b": empty_b,
                    "share_a": jsonable(share_a),
                    "share_b": jsonable(share_b),
                }
            )
        if kinds_a[c] != kinds_b[c]:
            continue
        kind = kinds_a[c]
        if kind == "number":
            va, vb = a[c].dropna().astype(float), b[c].dropna().astype(float)
            if va.empty or vb.empty:
                continue
            d = _cohen_d(va, vb)
            numbers.append(
                {
                    "column": c,
                    "mean_a": jsonable(va.mean()),
                    "mean_b": jsonable(vb.mean()),
                    "shift": jsonable(vb.mean() - va.mean()),
                    "shift_pct": jsonable((vb.mean() - va.mean()) / abs(va.mean()) * 100)
                    if va.mean()
                    else None,
                    "std_diff": jsonable(d) if d is not None else None,
                    "size": _size(d),
                }
            )
        elif kind in ("text", "boolean"):
            sa = set(a[c].dropna().astype(str))
            sb = set(b[c].dropna().astype(str))
            new, gone = sorted(sb - sa), sorted(sa - sb)
            if new or gone:
                categories.append(
                    {
                        "column": c,
                        "new": new[:MAX_CATEGORIES],
                        "new_count": len(new),
                        "vanished": gone[:MAX_CATEGORIES],
                        "vanished_count": len(gone),
                    }
                )
    numbers.sort(key=lambda n: -abs(n["std_diff"] or 0))

    lines = [f"{name_a} has {len(a):,} rows and {name_b} has {len(b):,}."]
    if added:
        lines.append(f"{name_b} adds {', '.join(added)}.")
    if removed:
        lines.append(f"{name_b} no longer has {', '.join(removed)}.")
    if type_changes:
        lines.append(
            "Type changes: "
            + "; ".join(f"{t['column']} ({t['from']} → {t['to']})" for t in type_changes)
            + "."
        )
    big = [n for n in numbers if n["size"] in ("medium", "large")]
    if big:
        top = big[0]
        lines.append(
            f"The biggest shift is in {top['column']}: the mean goes from "
            f"{fmt_number(top['mean_a'])} to {fmt_number(top['mean_b'])} ({top['size']} change)."
        )
    for cat in categories[:2]:
        if cat["new_count"]:
            lines.append(f"{cat['column']} has new values: {', '.join(cat['new'][:5])}.")
        if cat["vanished_count"]:
            lines.append(f"{cat['column']} no longer has: {', '.join(cat['vanished'][:5])}.")
    return {
        "a": {"name": name_a, "rows": len(a), "columns": len(cols_a)},
        "b": {"name": name_b, "rows": len(b), "columns": len(cols_b)},
        "added": added,
        "removed": removed,
        "type_changes": type_changes,
        "numbers": numbers,
        "categories": categories,
        "missing": missing,
        "summary": " ".join(lines),
    }
