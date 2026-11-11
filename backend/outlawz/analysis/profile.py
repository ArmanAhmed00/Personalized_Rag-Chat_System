"""The automatic overview of a CSV: the table at a glance, a card per column, findings worth
checking, the strongest correlations, and analyses to try in one click."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from outlawz.analysis.run import jsonable, plural
from outlawz.tabular import ColumnKind, fmt_number

HEAVY_GAPS = 0.2
IMBALANCE = 0.8
SKEW = 1.0
NEAR_DUPLICATE = 0.98


def _card(name: str, series: pd.Series, kind: ColumnKind, rows: int) -> dict[str, Any]:
    filled = int(series.notna().sum())
    card: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "filled": filled,
        "empty": rows - filled,
        "empty_share": jsonable((rows - filled) / rows if rows else 0.0),
        "distinct": int(series.nunique(dropna=True)),
    }
    values = series.dropna()
    if kind == "number" and len(values):
        v = values.astype(float)
        counts, edges = np.histogram(v.to_numpy(), bins=12 if np.ptp(v.to_numpy()) > 0 else 1)
        card.update(
            mean=jsonable(v.mean()),
            median=jsonable(v.median()),
            min=jsonable(v.min()),
            max=jsonable(v.max()),
            std=jsonable(v.std(ddof=1)) if len(v) > 1 else None,
            skew=jsonable(v.skew()) if len(v) > 2 else None,
            histogram=[
                {"from": jsonable(edges[i]), "to": jsonable(edges[i + 1]), "rows": int(c)}
                for i, c in enumerate(counts)
            ],
        )
    elif kind == "date" and len(values):
        months = values.dt.strftime("%Y-%m").value_counts().sort_index()
        if len(months) > 24:
            months = values.dt.strftime("%Y").value_counts().sort_index()
        card.update(
            min=jsonable(values.min()),
            max=jsonable(values.max()),
            histogram=[{"label": str(k), "rows": int(c)} for k, c in months.items()],
        )
    elif len(values):
        counts = values.astype(str).value_counts()
        card["top"] = [{"value": str(k), "rows": int(c)} for k, c in counts.head(6).items()]
    return card


def _findings(
    frame: pd.DataFrame, kinds: dict[str, ColumnKind], cards: list[dict[str, Any]], duplicates: int
) -> list[dict[str, Any]]:
    rows = len(frame)
    found: list[dict[str, Any]] = []
    if duplicates:
        found.append(
            {
                "kind": "duplicates",
                "message": f"{plural(duplicates, 'row')} "
                f"{'is an exact copy' if duplicates == 1 else 'are exact copies'} of another row.",
                "plan": None,
            }
        )
    for card in cards:
        name, kind = card["name"], card["kind"]
        if kind == "empty":
            found.append(
                {
                    "kind": "empty_column",
                    "column": name,
                    "message": f"{name} is empty.",
                    "plan": None,
                }
            )
            continue
        share = card["empty_share"] or 0
        if share >= HEAVY_GAPS:
            found.append(
                {
                    "kind": "gaps",
                    "column": name,
                    "message": f"{name} is empty in {fmt_number(round(100 * share))}% of rows.",
                    "plan": {"op": "missing"},
                }
            )
        if kind == "number" and card.get("skew") is not None and abs(card["skew"]) >= SKEW:
            side = "high" if card["skew"] > 0 else "low"
            found.append(
                {
                    "kind": "skew",
                    "column": name,
                    "message": f"{name} is skewed: a few {side} values pull the mean "
                    f"({fmt_number(card['mean'])}) away from the median "
                    f"({fmt_number(card['median'])}).",
                    "plan": {"op": "distribution", "column": name},
                }
            )
        if kind == "number" and card["filled"] >= 8:
            v = frame[name].dropna().astype(float)
            q1, q3 = v.quantile(0.25), v.quantile(0.75)
            spread = q3 - q1
            count = (
                int(((v < q1 - 1.5 * spread) | (v > q3 + 1.5 * spread)).sum()) if spread > 0 else 0
            )
            if count:
                found.append(
                    {
                        "kind": "outliers",
                        "column": name,
                        "message": f"{name} has {plural(count, 'unusual value')} "
                        "far from the rest.",
                        "plan": {"op": "outliers", "column": name},
                    }
                )
        if kind in ("text", "boolean") and card.get("top") and card["distinct"] >= 2:
            top = card["top"][0]
            if card["filled"] and top["rows"] / card["filled"] >= IMBALANCE:
                found.append(
                    {
                        "kind": "imbalance",
                        "column": name,
                        "message": f"{name} is mostly “{top['value']}” "
                        f"({fmt_number(round(100 * top['rows'] / card['filled']))}% "
                        "of filled rows).",
                        "plan": {"op": "distribution", "column": name},
                    }
                )
    found.extend(_near_duplicates(frame, kinds, rows))
    return found


def _near_duplicates(
    frame: pd.DataFrame, kinds: dict[str, ColumnKind], rows: int
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    names = [c for c, k in kinds.items() if k != "empty"]
    if rows < 3 or len(names) > 60:
        return found
    numbers = [c for c in names if kinds[c] == "number"]
    if len(numbers) >= 2:
        corr = frame[numbers].astype(float).corr(min_periods=3)
        for i, a in enumerate(numbers):
            for b in numbers[i + 1 :]:
                r = corr.loc[a, b]
                if not pd.isna(r) and abs(r) >= NEAR_DUPLICATE:
                    found.append(
                        {
                            "kind": "near_duplicate",
                            "columns": [a, b],
                            "message": f"{a} and {b} move almost exactly together "
                            f"(r = {fmt_number(round(float(r), 3))}); one may repeat the other.",
                            "plan": {"op": "relationship", "x": a, "y": b},
                        }
                    )
    others = [c for c in names if kinds[c] != "number"]
    for i, a in enumerate(others):
        for b in others[i + 1 :]:
            both = frame[[a, b]].dropna()
            if (
                len(both) >= 3
                and (both[a].astype(str) == both[b].astype(str)).mean() >= NEAR_DUPLICATE
            ):
                found.append(
                    {
                        "kind": "near_duplicate",
                        "columns": [a, b],
                        "message": f"{a} and {b} hold the same values in almost every row.",
                        "plan": None,
                    }
                )
    return found


def _top_correlations(frame: pd.DataFrame, kinds: dict[str, ColumnKind]) -> list[dict[str, Any]]:
    numbers = [c for c, k in kinds.items() if k == "number"][:25]
    if len(numbers) < 2:
        return []
    corr = frame[numbers].astype(float).corr(min_periods=3)
    pairs = []
    for i, a in enumerate(numbers):
        for b in numbers[i + 1 :]:
            r = corr.loc[a, b]
            if not pd.isna(r):
                pairs.append({"x": a, "y": b, "r": jsonable(float(r))})
    pairs.sort(key=lambda p: -abs(p["r"]))
    return pairs[:5]


def _suggestions(
    frame: pd.DataFrame, kinds: dict[str, ColumnKind], correlations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    numbers = [c for c, k in kinds.items() if k == "number"]
    dates = [c for c, k in kinds.items() if k == "date"]
    groups = [
        c
        for c, k in kinds.items()
        if k in ("text", "boolean") and 2 <= frame[c].nunique(dropna=True) <= 12
    ]
    out: list[dict[str, Any]] = []

    def add(title: str, plan: dict[str, Any]) -> None:
        if len(out) < 6:
            out.append({"title": title, "plan": plan})

    measure = numbers[0] if numbers else None
    if measure and groups:
        add(
            f"Total {measure} by {groups[0]}",
            {"op": "aggregate", "agg": "sum", "column": measure, "by": [groups[0]]},
        )
    if measure and dates:
        add(
            f"Average {measure} per month",
            {
                "op": "aggregate",
                "agg": "mean",
                "column": measure,
                "period": "month",
                "date_column": dates[0],
            },
        )
    elif dates:
        add(
            "Rows per month",
            {"op": "aggregate", "agg": "count", "period": "month", "date_column": dates[0]},
        )
    if measure and groups:
        add(
            f"Does {measure} differ by {groups[0]}?",
            {"op": "compare_groups", "column": measure, "group": groups[0]},
        )
    if correlations:
        top = correlations[0]
        add(f"{top['y']} against {top['x']}", {"op": "relationship", "x": top["x"], "y": top["y"]})
    if measure:
        add(f"Distribution of {measure}", {"op": "distribution", "column": measure})
    if len(groups) >= 2:
        add(
            f"{groups[0]} by {groups[1]}",
            {"op": "crosstab", "rows": groups[0], "columns": groups[1]},
        )
    if not out:
        add("Summary of the columns", {"op": "summary"})
    return out


def overview(frame: pd.DataFrame, kinds: dict[str, ColumnKind]) -> dict[str, Any]:
    rows = len(frame)
    duplicates = int(frame.duplicated().sum()) if rows else 0
    empty_cells = int(frame.isna().sum().sum())
    cards = [_card(c, frame[c], kinds[c], rows) for c in frame.columns]
    correlations = _top_correlations(frame, kinds)
    return {
        "rows": rows,
        "columns": len(frame.columns),
        "duplicates": duplicates,
        "empty_cells": empty_cells,
        "empty_share": jsonable(
            empty_cells / (rows * len(frame.columns)) if rows and len(frame.columns) else 0.0
        ),
        "cards": cards,
        "findings": _findings(frame, kinds, cards, duplicates),
        "correlations": correlations,
        "suggestions": _suggestions(frame, kinds, correlations),
    }
