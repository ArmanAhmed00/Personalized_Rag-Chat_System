"""Chart specs, built and checked on the server. The browser only draws what passes here.

Rules:

* every field a chart uses exists in every data row, with the right type;
* there's one value axis;
* a line needs an ordered x (numbers, dates or an ordered label like months);
* at most 40 bars, 8 series, 3 scatter colours plus "Other", and 25×25 heatmap cells.

When a chart doesn't fit, :func:`fit_chart` returns the right one with a note ("Showing the 40
largest of 87 groups", "Too many points in time for bars, so this is a line"), or no chart and the
reason, so the table is shown instead.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChartType = Literal["bar", "line", "scatter", "histogram", "heatmap"]
XKind = Literal["category", "ordered", "number", "time"]

MAX_BARS = 40
MAX_SERIES = 8
MAX_COLOURS = 3
MAX_HEAT = 25
MAX_POINTS = 2000
OTHER = "Other"


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ChartType
    title: str = Field(max_length=200)
    x: str
    y: str  # the one value axis (for heatmaps: the second category; the colour is ``value``)
    series: str | None = None  # bars/lines: one series per value; scatter: colour
    value: str | None = None  # heatmaps only
    x_kind: XKind = "category"
    x_label: str = ""
    y_label: str = ""
    data: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ChartError(ValueError):
    pass


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and not (isinstance(value, float) and (math.isnan(value) or math.isinf(value)))
    )


def _is_label(value: Any) -> bool:
    return isinstance(value, str | int | float | bool) and value is not None


def validate_chart(spec: ChartSpec) -> None:
    """Raise :class:`ChartError` if the spec breaks a rule. :func:`fit_chart` never returns a
    spec that fails this."""
    if not spec.data:
        raise ChartError("There's no data to plot.")
    fields = [spec.x, spec.y] + [f for f in (spec.series, spec.value) if f]
    for row in spec.data:
        for name in fields:
            if name not in row:
                raise ChartError(f"Every row needs the field '{name}'.")
    if spec.type in ("bar", "line", "histogram"):
        if not all(_is_number(r[spec.y]) or r[spec.y] is None for r in spec.data):
            raise ChartError(f"'{spec.y}' must hold numbers.")
        if spec.value is not None:
            raise ChartError("Only heatmaps have a separate value field.")
    if spec.type == "scatter":
        for name in (spec.x, spec.y):
            if not all(_is_number(r[name]) for r in spec.data):
                raise ChartError(f"'{name}' must hold numbers for a scatter plot.")
        if spec.series and len({r[spec.series] for r in spec.data}) > MAX_COLOURS + 1:
            raise ChartError(f"A scatter plot shows at most {MAX_COLOURS} colours plus Other.")
        if len(spec.data) > MAX_POINTS:
            raise ChartError(f"A scatter plot shows at most {MAX_POINTS} points.")
    if spec.type == "histogram" and not all(_is_number(r[spec.x]) for r in spec.data):
        raise ChartError("A histogram needs numeric bins.")
    if spec.type == "heatmap":
        if spec.value is None:
            raise ChartError("A heatmap needs a value field.")
        if not all(_is_number(r[spec.value]) or r[spec.value] is None for r in spec.data):
            raise ChartError(f"'{spec.value}' must hold numbers.")
        if (
            len({r[spec.x] for r in spec.data}) > MAX_HEAT
            or len({r[spec.y] for r in spec.data}) > MAX_HEAT
        ):
            raise ChartError(f"A heatmap shows at most {MAX_HEAT}×{MAX_HEAT} cells.")
    if spec.type == "line":
        if spec.x_kind == "category":
            raise ChartError("A line needs an ordered x axis.")
        _check_sorted(spec)
    if spec.type in ("bar", "line"):
        if not all(_is_label(r[spec.x]) for r in spec.data):
            raise ChartError(f"'{spec.x}' needs a label in every row.")
        series = {r[spec.series] for r in spec.data} if spec.series else {None}
        if len(series) > MAX_SERIES:
            raise ChartError(f"At most {MAX_SERIES} series can be shown.")
        if spec.type == "bar" and len({r[spec.x] for r in spec.data}) > MAX_BARS:
            raise ChartError(f"At most {MAX_BARS} bars can be shown.")


def _check_sorted(spec: ChartSpec) -> None:
    if spec.x_kind == "ordered":
        return  # the order given is the order (Jan, Feb, ...)
    seen: dict[Any, Any] = {}
    for row in spec.data:
        key = row.get(spec.series) if spec.series is not None else None
        value = row[spec.x]
        if key in seen and value < seen[key]:
            raise ChartError("A line's x values must be in order.")
        seen[key] = value


def fit_chart(spec: ChartSpec) -> tuple[ChartSpec | None, str | None]:
    """Make a spec fit the rules. Returns ``(spec, None)`` or ``(None, reason)``."""
    data = [dict(r) for r in spec.data]
    if not data:
        return None, "There's nothing to plot after the filters."
    notes = list(spec.notes)
    spec = spec.model_copy(update={"data": data, "notes": notes})

    if spec.type in ("bar", "line") and all(r.get(spec.y) is None for r in data):
        return None, "Every value is empty, so there's nothing to plot."

    if spec.type == "line" and spec.x_kind == "category":
        spec = spec.model_copy(update={"type": "bar"})
        notes.append("The groups have no natural order, so this is a bar chart.")

    if spec.type in ("bar", "line") and spec.series:
        spec = _limit_series(spec, notes)

    if spec.type == "bar":
        labels = list(dict.fromkeys(r[spec.x] for r in spec.data))
        if len(labels) > MAX_BARS:
            if spec.x_kind in ("time", "number", "ordered"):
                spec = spec.model_copy(update={"type": "line"})
                notes.append("There are too many points for bars, so this is a line.")
            else:
                totals: dict[Any, float] = defaultdict(float)
                for r in spec.data:
                    totals[r[spec.x]] += abs(r[spec.y] or 0)
                keep = set(sorted(labels, key=lambda v: -totals[v])[:MAX_BARS])
                spec = spec.model_copy(update={"data": [r for r in spec.data if r[spec.x] in keep]})
                notes.append(f"Showing the {MAX_BARS} largest of {len(labels)} groups.")

    if spec.type == "line":
        spec = _sort_line(spec)

    if spec.type == "scatter":
        rows = [r for r in spec.data if _is_number(r[spec.x]) and _is_number(r[spec.y])]
        if len(rows) < 2:
            return None, "There aren't enough pairs of numbers to plot."
        if spec.series:
            counts = Counter(r[spec.series] for r in rows)
            if len(counts) > MAX_COLOURS:
                top = {v for v, _ in counts.most_common(MAX_COLOURS)}
                rows = [
                    {**r, spec.series: r[spec.series] if r[spec.series] in top else OTHER}
                    for r in rows
                ]
                notes.append(f"Colours show the {MAX_COLOURS} largest groups; the rest are Other.")
        if len(rows) > MAX_POINTS:
            step = len(rows) / MAX_POINTS
            rows = [rows[int(i * step)] for i in range(MAX_POINTS)]
            notes.append(f"Showing {MAX_POINTS:,} evenly spread points of {len(spec.data):,}.")
        spec = spec.model_copy(update={"data": rows})

    if spec.type == "heatmap":
        spec = _limit_heatmap(spec, notes)

    spec.notes = list(dict.fromkeys(notes))
    validate_chart(spec)
    return spec, None


def _limit_series(spec: ChartSpec, notes: list[str]) -> ChartSpec:
    assert spec.series is not None
    names = list(dict.fromkeys(r[spec.series] for r in spec.data))
    if len(names) <= MAX_SERIES:
        return spec
    totals: dict[Any, float] = defaultdict(float)
    for r in spec.data:
        totals[r[spec.series]] += abs(r[spec.y] or 0)
    keep = set(sorted(names, key=lambda v: -totals[v])[:MAX_SERIES])
    notes.append(f"Showing the {MAX_SERIES} largest of {len(names)} series.")
    return spec.model_copy(update={"data": [r for r in spec.data if r[spec.series] in keep]})


def _sort_line(spec: ChartSpec) -> ChartSpec:
    if spec.x_kind == "ordered":
        return spec
    series = spec.series

    def order(row: dict[str, Any]) -> tuple[str, Any]:
        return (str(row.get(series)) if series is not None else "", row[spec.x])

    try:
        data = sorted(spec.data, key=order)
    except TypeError:
        return spec
    return spec.model_copy(update={"data": data})


def _limit_heatmap(spec: ChartSpec, notes: list[str]) -> ChartSpec:
    assert spec.value is not None
    value = spec.value
    data = spec.data
    trimmed = False
    for axis in (spec.x, spec.y):
        totals: dict[Any, float] = defaultdict(float)
        for r in data:
            totals[r[axis]] += abs(r[value] or 0)
        if len(totals) > MAX_HEAT:
            keep = set(sorted(totals, key=lambda v: -totals[v])[:MAX_HEAT])
            data = [r for r in data if r[axis] in keep]
            trimmed = True
    if trimmed:
        notes.append(f"Showing the {MAX_HEAT} largest categories on each side.")
    return spec.model_copy(update={"data": data})
