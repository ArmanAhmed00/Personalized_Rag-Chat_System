"""Check a plan against the real table before anything runs.

Harmless differences are fixed (case, stray spaces, underscores vs spaces: ``Region `` →
``region``). Anything else is a precise, plain error: "There's no column 'regoin'. Did you mean
'region'?". Types are checked too: a sum needs a number column, a period needs a date column.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping
from typing import Any

import pandas as pd

from outlawz.analysis.plans import (
    Aggregate,
    CompareGroups,
    Correlation,
    Crosstab,
    Distribution,
    Filter,
    Missing,
    Outliers,
    Plan,
    PlanError,
    Relationship,
    Rows,
    Summary,
)
from outlawz.tabular import ColumnKind, parse_number

NUMERIC_AGGS = {"sum", "mean", "median", "std"}
ORDERED_AGGS = {"min", "max"}  # numbers or dates
CATEGORY_KINDS: set[ColumnKind] = {"text", "boolean"}


def _key(name: str) -> str:
    return re.sub(r"[\s_\-]+", " ", name.strip().lower())


class Columns:
    """Resolves the names a plan uses to the table's real column names."""

    def __init__(self, kinds: Mapping[str, ColumnKind]) -> None:
        self.kinds = dict(kinds)
        self._by_key: dict[str, str] = {}
        for name in kinds:
            self._by_key.setdefault(_key(name), name)
        self.problems: list[str] = []
        self.fixes: list[str] = []

    def resolve(self, name: str | None, *, role: str = "column") -> str | None:
        if name is None:
            return None
        if name in self.kinds:
            return name
        found = self._by_key.get(_key(name))
        if found is not None:
            return found
        close = difflib.get_close_matches(name, list(self.kinds), n=1, cutoff=0.6) or (
            difflib.get_close_matches(_key(name), list(self._by_key), n=1, cutoff=0.6)
        )
        hint = ""
        if close:
            real = self._by_key.get(close[0], close[0])
            hint = f" Did you mean '{real}'?"
        self.problems.append(f"There's no column '{name[:80]}'.{hint}")
        return None

    def kind(self, name: str | None) -> ColumnKind | None:
        return self.kinds.get(name) if name else None

    def need(self, name: str | None, allowed: set[ColumnKind], what: str) -> None:
        kind = self.kind(name)
        if name is None or kind is None:
            return
        if kind not in allowed:
            self.problems.append(f"'{name}' holds {_kind_word(kind)}, but {what}.")


def _kind_word(kind: ColumnKind) -> str:
    return {
        "number": "numbers",
        "date": "dates",
        "boolean": "yes/no values",
        "text": "text",
        "empty": "no values",
    }[kind]


def _coerce(value: Any, kind: ColumnKind | None) -> Any:
    if isinstance(value, list):
        return [_coerce(v, kind) for v in value]
    if kind == "number" and not isinstance(value, bool):
        number = parse_number(value)
        return number if number is not None else value
    return value


def _check_filter(f: Filter, cols: Columns) -> Filter | None:
    column = cols.resolve(f.column)
    if column is None:
        return None
    kind = cols.kind(column)
    value = _coerce(f.value, kind)
    if f.op in ("gt", "gte", "lt", "lte", "between"):
        values = value if isinstance(value, list) else [value]
        if kind == "number" and not all(
            isinstance(v, int | float) and not isinstance(v, bool) for v in values
        ):
            cols.problems.append(f"The filter on '{column}' needs numbers to compare with.")
            return None
        if kind == "date":
            parsed = pd.to_datetime(pd.Series([str(v) for v in values]), errors="coerce")
            if parsed.isna().any():
                cols.problems.append(f"The filter on '{column}' needs dates like 2024-03-31.")
                return None
        if kind in ("text", "boolean", "empty"):
            cols.problems.append(
                f"'{column}' holds {_kind_word(kind or 'text')}, so '{f.op}' doesn't apply. "
                f"Use eq, in or contains."
            )
            return None
    if f.op == "contains" and kind not in ("text",):
        cols.problems.append(f"'contains' only works on text columns, not '{column}'.")
        return None
    return Filter(column=column, op=f.op, value=value)


def validate_plan(plan: Plan, kinds: Mapping[str, ColumnKind]) -> tuple[Plan, list[str]]:
    """Return the plan with real column names, and notes about any harmless fixes.
    Raise :class:`PlanError` listing every problem found."""
    cols = Columns(kinds)
    filters = [f for f in (_check_filter(f, cols) for f in plan.filters) if f is not None]
    checked = _check(plan, cols)
    if cols.problems:
        raise PlanError(list(dict.fromkeys(cols.problems)))
    assert checked is not None
    return checked.model_copy(update={"filters": filters}), cols.fixes


def _check(plan: Plan, cols: Columns) -> Plan | None:
    numbers: set[ColumnKind] = {"number"}
    if isinstance(plan, Aggregate):
        column = cols.resolve(plan.column)
        by = [b for b in (cols.resolve(b) for b in plan.by) if b is not None]
        date_column = cols.resolve(plan.date_column)
        if plan.agg in NUMERIC_AGGS:
            cols.need(column, numbers, f"'{plan.agg}' needs a number column")
        elif plan.agg in ORDERED_AGGS:
            cols.need(column, {"number", "date"}, f"'{plan.agg}' needs numbers or dates")
        if plan.period:
            if date_column is None and plan.date_column is None:
                dates = [c for c, k in cols.kinds.items() if k == "date"]
                if len(dates) == 1:
                    date_column = dates[0]
                    cols.fixes.append(f"Used '{date_column}' for the dates.")
                elif not dates:
                    cols.problems.append(f"Grouping by {plan.period} needs a date column.")
                else:
                    cols.problems.append(
                        f"Say which date column to group by {plan.period}: {', '.join(dates)}."
                    )
            else:
                cols.need(date_column, {"date"}, f"grouping by {plan.period} needs dates")
        for b in by:
            cols.need(b, {"text", "boolean", "number", "date"}, "it can't be grouped")
        return plan.model_copy(update={"column": column, "by": by, "date_column": date_column})
    if isinstance(plan, CompareGroups):
        column = cols.resolve(plan.column)
        group = cols.resolve(plan.group)
        kind = cols.kind(column)
        if plan.test in ("t_test", "anova"):
            cols.need(column, numbers, f"a {plan.test.replace('_', '-')} compares numbers")
        if plan.test == "chi_square":
            cols.need(column, CATEGORY_KINDS, "chi-square compares categories")
        if kind == "empty":
            cols.problems.append(f"'{column}' has no values to compare.")
        if column is not None and column == group:
            cols.problems.append("Choose two different columns: one with values, one with groups.")
        return plan.model_copy(update={"column": column, "group": group})
    if isinstance(plan, Relationship):
        x, y = cols.resolve(plan.x), cols.resolve(plan.y)
        color = cols.resolve(plan.color)
        cols.need(x, numbers, "a relationship needs two number columns")
        cols.need(y, numbers, "a relationship needs two number columns")
        cols.need(color, CATEGORY_KINDS, "colour needs a category column")
        return plan.model_copy(update={"x": x, "y": y, "color": color})
    if isinstance(plan, Correlation):
        if plan.columns is None:
            return plan
        columns = [c for c in (cols.resolve(c) for c in plan.columns) if c is not None]
        for c in columns:
            cols.need(c, numbers, "correlation needs number columns")
        if len(columns) == 1:
            cols.problems.append("Correlation needs at least two number columns.")
        return plan.model_copy(update={"columns": columns})
    if isinstance(plan, Distribution):
        column = cols.resolve(plan.column)
        cols.need(column, {"number", "date", "text", "boolean"}, "it has no values")
        return plan.model_copy(update={"column": column})
    if isinstance(plan, Crosstab):
        row_col, col_col = cols.resolve(plan.rows), cols.resolve(plan.columns)
        cols.need(row_col, CATEGORY_KINDS | {"number", "date"}, "it can't be counted")
        cols.need(col_col, CATEGORY_KINDS | {"number", "date"}, "it can't be counted")
        if row_col is not None and row_col == col_col:
            cols.problems.append("Choose two different columns for a crosstab.")
        return plan.model_copy(update={"rows": row_col, "columns": col_col})
    if isinstance(plan, Outliers):
        column = cols.resolve(plan.column)
        cols.need(column, numbers, "outliers need a number column")
        return plan.model_copy(update={"column": column})
    if isinstance(plan, Summary | Rows):
        update: dict[str, Any] = {}
        if plan.columns is not None:
            update["columns"] = [c for c in (cols.resolve(c) for c in plan.columns) if c]
        if isinstance(plan, Rows) and plan.sort_by is not None:
            update["sort_by"] = cols.resolve(plan.sort_by)
        return plan.model_copy(update=update)
    if isinstance(plan, Missing):
        return plan
    raise PlanError(["That plan isn't on the menu."])  # pragma: no cover - the union is closed
