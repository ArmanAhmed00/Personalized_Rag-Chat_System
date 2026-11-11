"""Fixed pandas routines, one per plan on the menu. They run on every row of the CSV.

Nothing here evaluates text: filters are applied with plain comparisons, never ``DataFrame.query``
or ``eval``, so a plan can't smuggle code in through a column name or a value.

Each routine returns a :class:`Result`: a table, a checked chart (or the reason there isn't one),
the statistics, and a summary written from the computed numbers.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from outlawz.analysis import stats
from outlawz.analysis.charts import ChartSpec, fit_chart
from outlawz.analysis.numbers import numbers_in
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
from outlawz.tabular import ColumnKind, fmt_number

EMPTY_LABEL = "(empty)"
AGG_WORDS = {
    "count": "count",
    "sum": "total",
    "mean": "average",
    "median": "median",
    "min": "lowest",
    "max": "highest",
    "distinct": "number of different values",
    "std": "standard deviation",
}


@dataclass
class Result:
    op: str
    title: str
    columns: list[dict[str, str]]
    rows: list[list[Any]]
    summary: str
    rows_used: int
    rows_total: int
    chart: dict[str, Any] | None = None
    chart_note: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def numbers(self) -> list[float]:
        """Every number the result contains, for the numbers check."""
        values: list[float] = [float(self.rows_used), float(self.rows_total), float(len(self.rows))]
        for row in self.rows:
            for cell in row:
                values.extend(_cell_numbers(cell))
        for value in self.stats.values():
            values.extend(_cell_numbers(value))
        for f in self.plan.get("filters", []):
            raw = f.get("value")
            for item in raw if isinstance(raw, list) else [raw]:
                values.extend(_cell_numbers(item))
        return values


def _cell_numbers(cell: Any) -> list[float]:
    if isinstance(cell, bool) or cell is None:
        return []
    if isinstance(cell, int | float):
        return [] if (isinstance(cell, float) and math.isnan(cell)) else [float(cell)]
    if isinstance(cell, str):
        return [n.value for n in numbers_in(cell)]
    return []


# ---------------------------------------------------------------- helpers


def jsonable(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 10)
    if isinstance(value, pd.Timestamp | datetime):
        ts = pd.Timestamp(value)
        if ts is pd.NaT:
            return None
        return ts.date().isoformat() if ts == ts.normalize() else ts.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, pd.Period):
        return str(value)
    return value


def _label(value: Any, kind: ColumnKind | None) -> str:
    value = jsonable(value)
    if value is None:
        return EMPTY_LABEL
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float | int):
        return fmt_number(value)
    return str(value)


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1", "t"}:
        return True
    if text in {"false", "no", "n", "0", "f"}:
        return False
    return None


def _date_match(series: pd.Series, value: Any) -> pd.Series:
    text = str(value).strip()
    if len(text) == 4 and text.isdigit():
        return series.dt.year == int(text)
    if len(text) == 7 and text[4] == "-" and text[:4].isdigit() and text[5:].isdigit():
        return (series.dt.year == int(text[:4])) & (series.dt.month == int(text[5:]))
    target = pd.to_datetime(text, errors="coerce")
    if target is pd.NaT:
        return pd.Series(False, index=series.index)
    return series.dt.normalize() == target.normalize()


def _eq(series: pd.Series, kind: ColumnKind, value: Any) -> pd.Series:
    if kind == "number":
        try:
            return series == float(value)
        except (TypeError, ValueError):
            return pd.Series(False, index=series.index)
    if kind == "date":
        return _date_match(series, value)
    if kind == "boolean":
        flag = _bool_value(value)
        return series == flag if flag is not None else pd.Series(False, index=series.index)
    return series.astype("string").str.casefold() == str(value).casefold()


def _compare_value(kind: ColumnKind, value: Any) -> Any:
    if kind == "date":
        return pd.to_datetime(str(value))
    return float(value)


def apply_filters(
    frame: pd.DataFrame, kinds: dict[str, ColumnKind], filters: list[Filter]
) -> pd.DataFrame:
    mask = pd.Series(True, index=frame.index)
    for f in filters:
        series = frame[f.column]
        kind = kinds[f.column]
        if f.op == "is_null":
            current = series.isna()
        elif f.op == "not_null":
            current = series.notna()
        elif f.op == "eq":
            current = _eq(series, kind, f.value)
        elif f.op == "ne":
            current = ~_eq(series, kind, f.value).fillna(False).astype(bool) & series.notna()
        elif f.op in ("in", "not_in"):
            values = f.value if isinstance(f.value, list) else [f.value]
            current = pd.Series(False, index=frame.index)
            for v in values:
                current = current | _eq(series, kind, v).fillna(False).astype(bool)
            if f.op == "not_in":
                current = ~current & series.notna()
        elif f.op == "contains":
            current = (
                series.astype("string")
                .str.casefold()
                .str.contains(str(f.value).casefold(), regex=False)
            )
        elif f.op == "between":
            assert isinstance(f.value, list)
            low, high = (_compare_value(kind, v) for v in f.value)
            current = (series >= low) & (series <= high)
        else:
            target = _compare_value(kind, f.value)
            current = {
                "gt": series > target,
                "gte": series >= target,
                "lt": series < target,
                "lte": series <= target,
            }[f.op]
        mask &= current.fillna(False).astype(bool)
    return frame[mask]


def _period_key(series: pd.Series, period: str) -> pd.Series:
    if period == "day":
        return series.dt.strftime("%Y-%m-%d")
    if period == "week":
        return series.dt.to_period("W").dt.start_time.dt.strftime("%Y-%m-%d")
    if period == "month":
        return series.dt.strftime("%Y-%m")
    if period == "quarter":
        q = series.dt.quarter
        return series.dt.year.astype("Int64").astype(str) + " Q" + q.astype("Int64").astype(str)
    return series.dt.year.astype("Int64").astype(str)


def _agg_label(plan: Aggregate) -> str:
    if plan.agg == "count" and not plan.column:
        return "rows"
    return f"{plan.agg} of {plan.column}"


def _aggregate_series(series: pd.Series | None, groups: Any, agg: str) -> Any:
    if series is None:
        return groups.size()
    target = groups[series.name] if groups is not None else series
    if agg == "count":
        return target.count()
    if agg == "distinct":
        return target.nunique()
    return getattr(target, agg)()


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def _strength(r: float) -> str:
    size = abs(r)
    if size < 0.1:
        return "no clear"
    if size < 0.3:
        return "a weak"
    if size < 0.5:
        return "a moderate"
    return "a strong"


def _direction(r: float) -> str:
    return "positive" if r > 0 else "negative"


def _value_text(value: Any) -> str:
    value = jsonable(value)
    if isinstance(value, float | int) and not isinstance(value, bool):
        return fmt_number(value)
    return str(value)


# ---------------------------------------------------------------- routines


def run_plan(
    plan: Plan, frame: pd.DataFrame, kinds: dict[str, ColumnKind], *, name: str = "the table"
) -> Result:
    total = len(frame)
    work = apply_filters(frame, kinds, plan.filters)
    used = len(work)
    notes: list[str] = []
    if plan.filters:
        notes.append(f"{used:,} of {total:,} rows match the filters.")
    routine = _ROUTINES[type(plan)]
    result: Result = routine(plan, work, kinds, used, total)
    result.notes = notes + result.notes
    result.plan = plan.model_dump(mode="json")
    return result


def _empty(plan: Plan, used: int, total: int, title: str) -> Result:
    return Result(
        op=plan.op,
        title=title,
        columns=[],
        rows=[],
        summary="No rows match these filters, so there's nothing to compute.",
        rows_used=used,
        rows_total=total,
        chart_note="There's nothing to plot after the filters.",
    )


def _aggregate(
    plan: Aggregate, work: pd.DataFrame, kinds: dict[str, ColumnKind], used: int, total: int
) -> Result:
    value_name = _agg_label(plan)
    title = AGG_WORDS[plan.agg].capitalize() + (f" of {plan.column}" if plan.column else " rows")
    if plan.by:
        title += " by " + " and ".join(plan.by)
    if plan.period:
        title += f" per {plan.period}"
    if used == 0:
        if plan.agg in ("count", "distinct") and not plan.by and not plan.period:
            return Result(
                op=plan.op,
                title=title,
                columns=[{"name": value_name, "kind": "number"}],
                rows=[[0]],
                summary="No rows match these filters, so the count is 0.",
                rows_used=0,
                rows_total=total,
                chart_note="A single number doesn't need a chart.",
                stats={"value": 0},
            )
        return _empty(plan, used, total, title)

    keys: list[str] = []
    frame = pd.DataFrame(index=work.index)
    for b in plan.by:
        frame[b] = work[b].map(lambda v, k=kinds[b]: _label(v, k))
        keys.append(b)
    if plan.period:
        assert plan.date_column is not None
        dates = work[plan.date_column]
        period_name = plan.period
        frame[period_name] = _period_key(dates, plan.period)
        keys.append(period_name)
        missing_dates = int(dates.isna().sum())
        if missing_dates:
            frame = frame[dates.notna()]
    if plan.column:
        frame[plan.column] = work[plan.column]
    series = frame[plan.column] if plan.column else None
    notes: list[str] = []
    if plan.period and missing_dates:
        notes.append(
            f"{plural(missing_dates, 'row')} without a date "
            f"{'isn' if missing_dates == 1 else 'aren'}'t counted."
        )

    if not keys:
        value = _aggregate_series(series, None, plan.agg) if series is not None else len(frame)
        value = jsonable(value)
        word = AGG_WORDS[plan.agg]
        what = f"of {plan.column}" if plan.column else "rows"
        summary = f"The {word} {what} is {_value_text(value)}" + (
            f" (over {used:,} rows)." if plan.agg != "count" else "."
        )
        if plan.agg == "count" and not plan.column:
            summary = f"There are {used:,} rows" + (
                " matching the filters." if plan.filters else "."
            )
        return Result(
            op=plan.op,
            title=title,
            columns=[{"name": value_name, "kind": "number"}],
            rows=[[value]],
            summary=summary,
            rows_used=used,
            rows_total=total,
            chart_note="A single number doesn't need a chart.",
            stats={"value": value},
            notes=notes,
        )

    groups = frame.groupby(keys, sort=False, dropna=False)
    values = _aggregate_series(series, groups, plan.agg)
    table = values.reset_index()
    table.columns = [*keys, value_name]
    group_count = len(table)
    if plan.period or plan.sort == "label":
        table = _sort_labels(table, keys, kinds, plan)
    else:
        table = table.sort_values(
            value_name, ascending=plan.sort == "value_asc", na_position="last", kind="mergesort"
        )
    if group_count > plan.limit:
        table = table.head(plan.limit)
        notes.append(f"Showing {plan.limit} of {group_count:,} groups.")

    columns = [
        {"name": k, "kind": "date" if k == plan.period else _kind_name(kinds.get(k))} for k in keys
    ]
    value_kind = (
        "date"
        if plan.column and kinds.get(plan.column) == "date" and plan.agg in ("min", "max")
        else "number"
    )
    columns.append({"name": value_name, "kind": value_kind})
    rows = [[jsonable(v) for v in row] for row in table.itertuples(index=False, name=None)]

    chart = None
    chart_note = None
    if value_kind == "number":
        data = [dict(zip([c["name"] for c in columns], row, strict=True)) for row in rows]
        if plan.period:
            series_key = plan.by[0] if plan.by else None
            spec = ChartSpec(
                type="line",
                title=title,
                x=plan.period,
                y=value_name,
                series=series_key,
                x_kind="time",
                x_label=plan.period,
                y_label=value_name,
                data=data,
            )
        else:
            x = keys[0]
            spec = ChartSpec(
                type="bar",
                title=title,
                x=x,
                y=value_name,
                series=keys[1] if len(keys) > 1 else None,
                x_kind="ordered"
                if plan.sort == "label" and kinds.get(x) in ("number", "date")
                else "category",
                x_label=x,
                y_label=value_name,
                data=data,
            )
        fitted, chart_note = fit_chart(spec)
        chart = fitted.model_dump(mode="json") if fitted else None
    else:
        chart_note = "Dates are shown as a table."

    summary = _aggregate_summary(plan, rows, keys, value_name, group_count)
    return Result(
        op=plan.op,
        title=title,
        columns=columns,
        rows=rows,
        summary=summary,
        rows_used=used,
        rows_total=total,
        chart=chart,
        chart_note=chart_note,
        stats={"groups": group_count},
        notes=notes,
    )


def _kind_name(kind: ColumnKind | None) -> str:
    return "text" if kind in (None, "boolean", "empty") else kind


def _sort_labels(
    table: pd.DataFrame, keys: list[str], kinds: dict[str, ColumnKind], plan: Aggregate
) -> pd.DataFrame:
    def sort_key(column: pd.Series) -> pd.Series:
        if column.name in kinds and kinds[str(column.name)] == "number":
            return pd.to_numeric(column.str.replace(",", ""), errors="coerce")
        return column

    order: list[str] = [plan.period] if plan.period else []
    order += [k for k in keys if k not in order]
    return table.sort_values(order, key=sort_key, kind="mergesort")


def _aggregate_summary(
    plan: Aggregate, rows: list[list[Any]], keys: list[str], value_name: str, group_count: int
) -> str:
    numeric = [
        (r, r[-1]) for r in rows if isinstance(r[-1], int | float) and not isinstance(r[-1], bool)
    ]
    word = AGG_WORDS[plan.agg]
    what = f"{word} of {plan.column}" if plan.column else "number of rows"
    if not numeric:
        return f"{what.capitalize()} for {group_count:,} groups."

    def name(row: list[Any]) -> str:
        return ", ".join(str(v) for v in row[: len(keys)])

    high = max(numeric, key=lambda x: x[1])
    low = min(numeric, key=lambda x: x[1])
    if plan.period and not plan.by:
        first, last = rows[0], rows[-1]
        return (
            f"The {what} went from {_value_text(first[-1])} in {first[0]} to "
            f"{_value_text(last[-1])} in {last[0]}. The highest was {_value_text(high[1])} "
            f"({name(high[0])}) and the lowest {_value_text(low[1])} ({name(low[0])})."
        )
    if len(numeric) == 1:
        return f"The {what} for {name(high[0])} is {_value_text(high[1])}."
    return (
        f"The {what} is highest for {name(high[0])} ({_value_text(high[1])}) and lowest for "
        f"{name(low[0])} ({_value_text(low[1])}), across {group_count:,} groups."
    )


def _compare_groups(
    plan: CompareGroups, work: pd.DataFrame, kinds: dict[str, ColumnKind], used: int, total: int
) -> Result:
    title = f"{plan.column} compared across {plan.group}"
    data = work[[plan.group, plan.column]].copy()
    data["_group"] = data[plan.group].map(lambda v: _label(v, kinds[plan.group]))
    if plan.groups:
        wanted = {_label(g, kinds[plan.group]).casefold() for g in plan.groups}
        data = data[data["_group"].str.casefold().isin(wanted)]
    data = data[data[plan.column].notna()]
    if data.empty:
        return _empty(plan, used, total, title)
    kind = kinds[plan.column]
    names = sorted(data["_group"].unique(), key=str)
    if len(names) < 2:
        raise PlanError(
            [f"Only one group of '{plan.group}' has values, so there's nothing to compare."]
        )
    if len(names) > 50:
        raise PlanError(
            [f"'{plan.group}' has {len(names)} groups; compare at most 50 (pick some with groups)."]
        )
    notes: list[str] = []

    if kind == "number" and plan.test != "chi_square":
        per = {
            g: data.loc[data["_group"] == g, plan.column].astype(float).to_numpy() for g in names
        }
        rows = [
            [
                g,
                len(v),
                jsonable(v.mean()),
                jsonable(np.median(v)),
                jsonable(v.std(ddof=1) if len(v) > 1 else None),
            ]
            for g, v in per.items()
        ]
        columns = [
            {"name": plan.group, "kind": "text"},
            {"name": "rows", "kind": "number"},
            {"name": f"mean of {plan.column}", "kind": "number"},
            {"name": f"median of {plan.column}", "kind": "number"},
            {"name": f"std of {plan.column}", "kind": "number"},
        ]
        test = plan.test
        if test == "auto":
            test = "t_test" if len(names) == 2 else "anova"
        if test == "t_test" and len(names) != 2:
            raise PlanError(
                [
                    f"A t-test compares two groups, but '{plan.group}' has {len(names)}. "
                    "Pick two with groups, or use anova."
                ]
            )
        result_stats: dict[str, Any]
        try:
            if test == "t_test":
                a, b = per[names[0]], per[names[1]]
                t = stats.welch_t(a, b)
                mw = stats.mann_whitney(a, b)
                result_stats = {
                    "test": "Welch's t-test",
                    "t": jsonable(t.t),
                    "df": jsonable(t.df),
                    "p": jsonable(t.p),
                    "difference": jsonable(t.difference),
                    "rank_test": "Mann–Whitney U",
                    "u": jsonable(mw.u),
                    "rank_p": jsonable(mw.p),
                }
                agree = (t.p < 0.05) == (mw.p < 0.05)
                summary = (
                    f"{names[0]} averages {_value_text(t.mean_a)} and {names[1]} "
                    f"{_value_text(t.mean_b)}, "
                    f"a difference of {_value_text(t.difference)}. Welch's t-test says this is "
                    f"{stats.describe_p(t.p)}; the rank check {'agrees' if agree else 'disagrees'} "
                    f"(p = {fmt_number(round(mw.p, 3))})."
                )
            else:
                anova = stats.one_way_anova(list(per.values()))
                result_stats = {
                    "test": "one-way ANOVA",
                    "f": jsonable(anova.f),
                    "df_between": anova.df_between,
                    "df_within": anova.df_within,
                    "p": jsonable(anova.p),
                    "eta_squared": jsonable(anova.eta_squared),
                }
                means = {g: float(v.mean()) for g, v in per.items()}
                hi, lo = max(means, key=means.__getitem__), min(means, key=means.__getitem__)
                summary = (
                    f"Across {len(names)} groups, the mean of {plan.column} ranges from "
                    f"{_value_text(means[lo])} ({lo}) to {_value_text(means[hi])} ({hi}). "
                    "ANOVA says the "
                    f"differences are {stats.describe_p(anova.p)}."
                )
        except ValueError as exc:
            raise PlanError([str(exc)]) from None
        chart_data = [{plan.group: r[0], f"mean of {plan.column}": r[2]} for r in rows]
        spec = ChartSpec(
            type="bar",
            title=f"Mean of {plan.column} by {plan.group}",
            x=plan.group,
            y=f"mean of {plan.column}",
            x_label=plan.group,
            y_label=f"mean of {plan.column}",
            data=chart_data,
        )
        fitted, note = fit_chart(spec)
        return Result(
            op=plan.op,
            title=title,
            columns=columns,
            rows=rows,
            summary=summary,
            rows_used=used,
            rows_total=total,
            chart=fitted.model_dump(mode="json") if fitted else None,
            chart_note=note,
            stats=result_stats,
            notes=notes,
        )

    # Categories: chi-square on the counts.
    if plan.test in ("t_test", "anova"):
        raise PlanError([f"'{plan.column}' isn't a number column, so use chi_square."])
    data["_value"] = data[plan.column].map(lambda v: _label(v, kind))
    table = pd.crosstab(data["_group"], data["_value"])
    try:
        chi = stats.chi_square_independence(table.to_numpy())
    except ValueError as exc:
        raise PlanError([str(exc)]) from None
    if chi.expected_min < 5:
        notes.append("Some expected counts are below 5, so the chi-square result is only rough.")
    values = [str(c) for c in table.columns]
    columns = [{"name": plan.group, "kind": "text"}] + [
        {"name": v, "kind": "number"} for v in values
    ]
    rows = [
        [str(idx), *[int(x) for x in row]]
        for idx, row in zip(table.index, table.to_numpy(), strict=True)
    ]
    summary = (
        f"How {plan.column} splits differs across {plan.group}: the difference is "
        f"{stats.describe_p(chi.p)} (chi-square = {fmt_number(round(chi.chi2, 2))}, "
        f"Cramér's V = {fmt_number(round(chi.cramers_v, 2))})."
    )
    long = [
        {plan.group: r[0], plan.column: v, "rows": n}
        for r in rows
        for v, n in zip(values, r[1:], strict=True)
    ]
    spec = ChartSpec(
        type="bar",
        title=f"{plan.column} by {plan.group}",
        x=plan.group,
        y="rows",
        series=plan.column,
        x_label=plan.group,
        y_label="rows",
        data=long,
    )
    fitted, note = fit_chart(spec)
    return Result(
        op=plan.op,
        title=title,
        columns=columns,
        rows=rows,
        summary=summary,
        rows_used=used,
        rows_total=total,
        chart=fitted.model_dump(mode="json") if fitted else None,
        chart_note=note,
        stats={
            "test": "chi-square",
            "chi2": jsonable(chi.chi2),
            "df": chi.df,
            "p": jsonable(chi.p),
            "cramers_v": jsonable(chi.cramers_v),
        },
        notes=notes,
    )


def _relationship(
    plan: Relationship, work: pd.DataFrame, kinds: dict[str, ColumnKind], used: int, total: int
) -> Result:
    title = f"{plan.y} against {plan.x}"
    cols = [plan.x, plan.y] + ([plan.color] if plan.color else [])
    pairs = work[cols].dropna(subset=[plan.x, plan.y])
    if len(pairs) < 3:
        raise PlanError([f"At least three rows need values in both '{plan.x}' and '{plan.y}'."])
    try:
        fit = stats.pearson(pairs[plan.x].astype(float), pairs[plan.y].astype(float))
    except ValueError as exc:
        raise PlanError([str(exc)]) from None
    rows = [
        ["pairs", fit.n],
        ["r", jsonable(fit.r)],
        ["r²", jsonable(fit.r_squared)],
        ["slope", jsonable(fit.slope)],
        ["intercept", jsonable(fit.intercept)],
        ["p-value", jsonable(fit.p)],
    ]
    summary = (
        f"{plan.x} and {plan.y} have {_strength(fit.r)} "
        f"{_direction(fit.r) + ' ' if abs(fit.r) >= 0.1 else ''}"
        f"relationship (r = {fmt_number(round(fit.r, 2))}, {fit.n:,} pairs). On average, "
        f"{plan.y} changes by "
        f"{_value_text(fit.slope)} for each 1 more in {plan.x}; this is {stats.describe_p(fit.p)}."
    )
    data = []
    for row in pairs.itertuples(index=False, name=None):
        point = {plan.x: jsonable(row[0]), plan.y: jsonable(row[1])}
        if plan.color:
            point[plan.color] = _label(row[2], kinds[plan.color])
        data.append(point)
    spec = ChartSpec(
        type="scatter",
        title=title,
        x=plan.x,
        y=plan.y,
        series=plan.color,
        x_kind="number",
        x_label=plan.x,
        y_label=plan.y,
        data=data,
    )
    fitted, note = fit_chart(spec)
    return Result(
        op=plan.op,
        title=title,
        columns=[{"name": "measure", "kind": "text"}, {"name": "value", "kind": "number"}],
        rows=rows,
        summary=summary,
        rows_used=used,
        rows_total=total,
        chart=fitted.model_dump(mode="json") if fitted else None,
        chart_note=note,
        stats={
            "r": jsonable(fit.r),
            "slope": jsonable(fit.slope),
            "intercept": jsonable(fit.intercept),
            "p": jsonable(fit.p),
            "n": fit.n,
        },
    )


def _correlation(
    plan: Correlation, work: pd.DataFrame, kinds: dict[str, ColumnKind], used: int, total: int
) -> Result:
    columns = plan.columns or [c for c, k in kinds.items() if k == "number"][:25]
    if len(columns) < 2:
        raise PlanError(
            ["Correlation needs at least two number columns, and this table doesn't have them."]
        )
    numeric = work[columns].astype(float)
    matrix = numeric.corr(method=plan.method, min_periods=3)
    pairs = []
    for i, a in enumerate(columns):
        for b in columns[i + 1 :]:
            r = matrix.loc[a, b]
            if not pd.isna(r):
                pairs.append((a, b, float(r)))
    pairs.sort(key=lambda p: -abs(p[2]))
    rows = [[a, b, jsonable(r)] for a, b, r in pairs]
    title = f"How the number columns move together ({plan.method})"
    if pairs:
        a, b, r = pairs[0]
        direction = f" {_direction(r)}" if abs(r) >= 0.1 else ""
        summary = (
            f"The strongest link is between {a} and {b}: {_strength(r)}{direction} "
            f"correlation (r = {fmt_number(round(r, 2))})."
        )
    else:
        summary = "There aren't enough overlapping values to compute correlations."
    heat = [
        {"column": a, "with": b, "r": jsonable(matrix.loc[a, b])} for a in columns for b in columns
    ]
    spec = ChartSpec(
        type="heatmap",
        title=title,
        x="column",
        y="with",
        value="r",
        x_label="",
        y_label="",
        data=heat,
    )
    fitted, note = fit_chart(spec)
    return Result(
        op=plan.op,
        title=title,
        columns=[
            {"name": "column", "kind": "text"},
            {"name": "with", "kind": "text"},
            {"name": "r", "kind": "number"},
        ],
        rows=rows,
        summary=summary,
        rows_used=used,
        rows_total=total,
        chart=fitted.model_dump(mode="json") if fitted else None,
        chart_note=note,
        stats={"pairs": len(pairs), "method": plan.method},
    )


def _distribution(
    plan: Distribution, work: pd.DataFrame, kinds: dict[str, ColumnKind], used: int, total: int
) -> Result:
    kind = kinds[plan.column]
    series = work[plan.column].dropna()
    title = f"Distribution of {plan.column}"
    if series.empty:
        return _empty(plan, used, total, title)
    empty = used - len(series)
    notes = (
        [f"{plural(empty, 'empty cell')} {'isn' if empty == 1 else 'aren'}'t counted."]
        if empty
        else []
    )
    if kind == "number":
        values = series.astype(float).to_numpy()
        counts, edges = np.histogram(values, bins=plan.bins if np.ptp(values) > 0 else 1)
        rows = [
            [jsonable(edges[i]), jsonable(edges[i + 1]), int(counts[i])] for i in range(len(counts))
        ]
        skew = float(series.astype(float).skew()) if len(series) > 2 else 0.0
        st = {
            "n": len(values),
            "mean": jsonable(values.mean()),
            "median": jsonable(np.median(values)),
            "std": jsonable(values.std(ddof=1)) if len(values) > 1 else None,
            "min": jsonable(values.min()),
            "max": jsonable(values.max()),
            "skew": jsonable(skew),
        }
        shape = "roughly symmetric"
        if skew > 1:
            shape = "skewed to the right (a long tail of high values)"
        elif skew < -1:
            shape = "skewed to the left (a long tail of low values)"
        summary = (
            f"{plan.column} runs from {_value_text(values.min())} to "
            f"{_value_text(values.max())}, with a median of {_value_text(np.median(values))} "
            f"and a mean of {_value_text(values.mean())}. The shape is {shape}."
        )
        data = [{"from": r[0], "to": r[1], "rows": r[2]} for r in rows]
        spec = ChartSpec(
            type="histogram",
            title=title,
            x="from",
            y="rows",
            x_kind="number",
            x_label=plan.column,
            y_label="rows",
            data=data,
        )
        fitted, note = fit_chart(spec)
        return Result(
            op=plan.op,
            title=title,
            columns=[
                {"name": "from", "kind": "number"},
                {"name": "to", "kind": "number"},
                {"name": "rows", "kind": "number"},
            ],
            rows=rows,
            summary=summary,
            rows_used=used,
            rows_total=total,
            chart=fitted.model_dump(mode="json") if fitted else None,
            chart_note=note,
            stats=st,
            notes=notes,
        )
    if kind == "date":
        by_month = series.dt.strftime("%Y-%m").value_counts().sort_index()
        rows = [[str(k), int(v)] for k, v in by_month.items()]
        summary = (
            f"{plan.column} runs from {jsonable(series.min())} to {jsonable(series.max())}; "
            f"the busiest month is {by_month.idxmax()} ({int(by_month.max()):,} rows)."
        )
        spec = ChartSpec(
            type="line" if len(rows) > 1 else "bar",
            title=title,
            x="month",
            y="rows",
            x_kind="time",
            x_label="month",
            y_label="rows",
            data=[{"month": r[0], "rows": r[1]} for r in rows],
        )
        fitted, note = fit_chart(spec)
        return Result(
            op=plan.op,
            title=title,
            columns=[{"name": "month", "kind": "date"}, {"name": "rows", "kind": "number"}],
            rows=rows,
            summary=summary,
            rows_used=used,
            rows_total=total,
            chart=fitted.model_dump(mode="json") if fitted else None,
            chart_note=note,
            stats={"n": len(series)},
            notes=notes,
        )
    counts = series.map(lambda v: _label(v, kind)).value_counts()
    rows = [[str(k), int(v)] for k, v in counts.items()]
    top, top_n = rows[0]
    summary = (
        f"{plan.column} has {len(rows):,} different values. The most common is {top} ({top_n:,} of "
        f"{len(series):,} rows, {fmt_number(round(100 * top_n / len(series), 1))}%)."
    )
    spec = ChartSpec(
        type="bar",
        title=title,
        x=plan.column,
        y="rows",
        x_label=plan.column,
        y_label="rows",
        data=[{plan.column: r[0], "rows": r[1]} for r in rows],
    )
    fitted, note = fit_chart(spec)
    return Result(
        op=plan.op,
        title=title,
        columns=[{"name": plan.column, "kind": "text"}, {"name": "rows", "kind": "number"}],
        rows=rows,
        summary=summary,
        rows_used=used,
        rows_total=total,
        chart=fitted.model_dump(mode="json") if fitted else None,
        chart_note=note,
        stats={"n": len(series), "distinct": len(rows)},
        notes=notes,
    )


def _crosstab(
    plan: Crosstab, work: pd.DataFrame, kinds: dict[str, ColumnKind], used: int, total: int
) -> Result:
    title = f"{plan.rows} by {plan.columns}"
    if used == 0:
        return _empty(plan, used, total, title)
    a = work[plan.rows].map(lambda v: _label(v, kinds[plan.rows]))
    b = work[plan.columns].map(lambda v: _label(v, kinds[plan.columns]))
    counts = pd.crosstab(a, b)
    shown = (
        counts
        if plan.normalize == "none"
        else pd.crosstab(
            a, b, normalize={"rows": "index", "columns": "columns", "all": "all"}[plan.normalize]
        )
    )
    if shown.shape[0] > 200 or shown.shape[1] > 50:
        raise PlanError(
            [
                f"That crosstab would have {shown.shape[0]}×{shown.shape[1]} cells; "
                "choose columns with fewer different values."
            ]
        )
    value_name = "rows" if plan.normalize == "none" else "share"
    columns = [{"name": plan.rows, "kind": "text"}] + [
        {"name": str(c), "kind": "number"} for c in shown.columns
    ]
    rows = [
        [str(idx), *[jsonable(x) for x in row]]
        for idx, row in zip(shown.index, shown.to_numpy(), strict=True)
    ]
    st: dict[str, Any] = {}
    try:
        chi = stats.chi_square_independence(counts.to_numpy())
        st = {
            "chi2": jsonable(chi.chi2),
            "df": chi.df,
            "p": jsonable(chi.p),
            "cramers_v": jsonable(chi.cramers_v),
        }
    except ValueError:
        pass
    stacked = counts.stack()
    (ra, cb), top = stacked.idxmax(), int(stacked.max())
    summary = (
        f"The most common combination is {plan.rows} {ra} with {plan.columns} {cb} ({top:,} rows)."
    )
    if st:
        link = stats.describe_p(float(st["p"]))
        summary += f" Any link between {plan.rows} and {plan.columns} is {link}."
    heat = [
        {plan.columns: str(c), plan.rows: str(r), value_name: jsonable(shown.loc[r, c])}
        for r in shown.index
        for c in shown.columns
    ]
    spec = ChartSpec(
        type="heatmap",
        title=title,
        x=plan.columns,
        y=plan.rows,
        value=value_name,
        x_label=plan.columns,
        y_label=plan.rows,
        data=heat,
    )
    fitted, note = fit_chart(spec)
    return Result(
        op=plan.op,
        title=title,
        columns=columns,
        rows=rows,
        summary=summary,
        rows_used=used,
        rows_total=total,
        chart=fitted.model_dump(mode="json") if fitted else None,
        chart_note=note,
        stats=st,
    )


def _outliers(
    plan: Outliers, work: pd.DataFrame, kinds: dict[str, ColumnKind], used: int, total: int
) -> Result:
    series = work[plan.column].dropna().astype(float)
    title = f"Unusual values of {plan.column}"
    if len(series) < 4:
        raise PlanError([f"'{plan.column}' needs at least four values to look for outliers."])
    if plan.method == "iqr":
        k = plan.threshold or 1.5
        q1, q3 = float(series.quantile(0.25)), float(series.quantile(0.75))
        spread = q3 - q1
        low, high = q1 - k * spread, q3 + k * spread
        rule = f"more than {fmt_number(k)} × IQR outside the middle half"
    else:
        k = plan.threshold or 3.0
        mean, std = float(series.mean()), float(series.std(ddof=1))
        low, high = (mean - k * std, mean + k * std) if std > 0 else (mean, mean)
        rule = f"more than {fmt_number(k)} standard deviations from the mean"
    flagged = series[(series < low) | (series > high)]
    order = (
        flagged.abs().sort_values(ascending=False).index
        if plan.method == "zscore"
        else flagged.sort_values().index
    )
    picked = work.loc[order[: plan.limit]]
    shown_cols = list(work.columns)[:12]
    columns = [{"name": "row", "kind": "number"}] + [
        {"name": c, "kind": _kind_name(kinds[c])} for c in shown_cols
    ]
    rows = [
        [int(idx) + 1, *[jsonable(v) for v in r]]
        for idx, r in zip(
            picked.index, picked[shown_cols].itertuples(index=False, name=None), strict=True
        )
    ]
    notes = (
        [f"Showing {plan.limit} of {len(flagged):,} outliers."] if len(flagged) > plan.limit else []
    )
    summary = (
        f"{len(flagged):,} of {len(series):,} values of {plan.column} are outliers ({rule}: below "
        f"{_value_text(low)} or above {_value_text(high)})."
    )
    if not len(flagged):
        summary = f"No values of {plan.column} are outliers ({rule})."
    counts, edges = np.histogram(series.to_numpy(), bins=20 if np.ptp(series.to_numpy()) > 0 else 1)
    spec = ChartSpec(
        type="histogram",
        title=f"Distribution of {plan.column}",
        x="from",
        y="rows",
        x_kind="number",
        x_label=plan.column,
        y_label="rows",
        data=[{"from": jsonable(edges[i]), "rows": int(counts[i])} for i in range(len(counts))],
    )
    fitted, note = fit_chart(spec)
    return Result(
        op=plan.op,
        title=title,
        columns=columns,
        rows=rows,
        summary=summary,
        rows_used=used,
        rows_total=total,
        chart=fitted.model_dump(mode="json") if fitted else None,
        chart_note=note,
        stats={
            "outliers": len(flagged),
            "low": jsonable(low),
            "high": jsonable(high),
            "method": plan.method,
        },
        notes=notes,
    )


def _missing(
    plan: Missing, work: pd.DataFrame, kinds: dict[str, ColumnKind], used: int, total: int
) -> Result:
    title = "Empty cells per column"
    counts = work.isna().sum()
    rows = [
        [c, int(counts[c]), jsonable(100 * counts[c] / used if used else 0.0)] for c in work.columns
    ]
    rows.sort(key=lambda r: -r[1])
    with_gaps = [r for r in rows if r[1]]
    any_missing = int(work.isna().any(axis=1).sum())
    if with_gaps:
        top = with_gaps[0]
        summary = (
            f"{len(with_gaps)} of {len(rows)} columns {'has' if len(with_gaps) == 1 else 'have'} "
            f"empty cells. The most is in {top[0]}: {top[1]:,} "
            f"({fmt_number(round(top[2], 1))}%). {plural(any_missing, 'row')} "
            f"{'has' if any_missing == 1 else 'have'} at least one empty cell."
        )
    else:
        summary = "No cells are empty."
    spec = ChartSpec(
        type="bar",
        title=title,
        x="column",
        y="empty %",
        x_label="column",
        y_label="empty %",
        data=[{"column": r[0], "empty %": r[2]} for r in with_gaps],
    )
    fitted, note = fit_chart(spec) if with_gaps else (None, "No cells are empty.")
    return Result(
        op=plan.op,
        title=title,
        columns=[
            {"name": "column", "kind": "text"},
            {"name": "empty", "kind": "number"},
            {"name": "empty %", "kind": "number"},
        ],
        rows=rows,
        summary=summary,
        rows_used=used,
        rows_total=total,
        chart=fitted.model_dump(mode="json") if fitted else None,
        chart_note=note,
        stats={"rows_with_gaps": any_missing},
    )


def _summary(
    plan: Summary, work: pd.DataFrame, kinds: dict[str, ColumnKind], used: int, total: int
) -> Result:
    names = plan.columns or list(work.columns)
    rows = []
    for c in names:
        s = work[c]
        kind = kinds[c]
        filled = int(s.notna().sum())
        row: list[Any] = [c, kind, filled, int(used - filled), int(s.nunique(dropna=True))]
        if kind == "number" and filled:
            v = s.astype(float)
            row += [
                jsonable(v.mean()),
                jsonable(v.median()),
                jsonable(v.min()),
                jsonable(v.max()),
                None,
            ]
        elif kind == "date" and filled:
            row += [None, None, jsonable(s.min()), jsonable(s.max()), None]
        elif filled:
            top = s.map(lambda x, k=kind: _label(x, k)).value_counts()
            row += [None, None, None, None, f"{top.index[0]} ({int(top.iloc[0]):,})"]
        else:
            row += [None, None, None, None, None]
        rows.append(row)
    columns = [
        {"name": n, "kind": k}
        for n, k in (
            ("column", "text"),
            ("type", "text"),
            ("filled", "number"),
            ("empty", "number"),
            ("different values", "number"),
            ("mean", "number"),
            ("median", "number"),
            ("min", "text"),
            ("max", "text"),
            ("most common", "text"),
        )
    ]
    kinds_count = {
        k: sum(1 for c in names if kinds[c] == k) for k in ("number", "text", "date", "boolean")
    }
    described = ", ".join(f"{n} {k}" for k, n in kinds_count.items() if n)
    summary = f"The table has {used:,} rows and {len(names)} columns ({described})."
    return Result(
        op=plan.op,
        title="Summary of the columns",
        columns=columns,
        rows=rows,
        summary=summary,
        rows_used=used,
        rows_total=total,
        chart_note="A summary is shown as a table.",
    )


def _rows(
    plan: Rows, work: pd.DataFrame, kinds: dict[str, ColumnKind], used: int, total: int
) -> Result:
    names = plan.columns or list(work.columns)
    data = work
    if plan.sort_by:
        data = data.sort_values(
            plan.sort_by, ascending=not plan.descending, na_position="last", kind="mergesort"
        )
    picked = data.head(plan.limit)
    columns = [{"name": "row", "kind": "number"}] + [
        {"name": c, "kind": _kind_name(kinds[c])} for c in names
    ]
    rows = [
        [int(idx) + 1, *[jsonable(v) for v in r]]
        for idx, r in zip(
            picked.index, picked[names].itertuples(index=False, name=None), strict=True
        )
    ]
    order = (
        f", sorted by {plan.sort_by} ({'highest' if plan.descending else 'lowest'} first)"
        if plan.sort_by
        else ""
    )
    summary = f"Showing {len(rows):,} of {used:,} rows{order}."
    if not rows:
        summary = "No rows match."
    return Result(
        op=plan.op,
        title="Rows",
        columns=columns,
        rows=rows,
        summary=summary,
        rows_used=used,
        rows_total=total,
        chart_note="Rows are shown as a table.",
    )


_ROUTINES: dict[type, Any] = {
    Aggregate: _aggregate,
    CompareGroups: _compare_groups,
    Relationship: _relationship,
    Correlation: _correlation,
    Distribution: _distribution,
    Crosstab: _crosstab,
    Outliers: _outliers,
    Missing: _missing,
    Summary: _summary,
    Rows: _rows,
}
