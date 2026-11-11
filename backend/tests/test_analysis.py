"""Data analysis: every operation against known answers, validation, chart rules, the numbers
check, the overview, Compare, and attempts to inject code through plans."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from outlawz.analysis import stats
from outlawz.analysis.charts import ChartError, ChartSpec, fit_chart, validate_chart
from outlawz.analysis.compare import compare_frames
from outlawz.analysis.numbers import numbers_in, unmatched_numbers
from outlawz.analysis.plans import NoPlan, PlanError, parse_plan, plan_from_dict
from outlawz.analysis.profile import overview
from outlawz.analysis.run import Result, run_plan
from outlawz.analysis.validate import validate_plan
from outlawz.tabular import load_csv, typed_frame

SAMPLES = Path(__file__).resolve().parents[1] / "outlawz" / "samples"

CSV = """region,channel,date,units,revenue,returned,note
North,Online,2024-01-05,2,20,no,
North,Store,2024-01-20,4,40,no,gift
South,Online,2024-02-03,1,10,yes,
South,Online,2024-02-14,3,30,no,
East,Store,2024-03-01,5,50,no,
East,Store,2024-03-15,5,50,no,gift wrap
West,Online,2024-03-30,,15,no,
"""


@pytest.fixture
def table() -> tuple[pd.DataFrame, dict[str, Any]]:
    raw = pd.read_csv(io.StringIO(CSV), dtype=str, keep_default_na=False, na_values=[""])
    return typed_frame(raw)


def run(table: tuple[pd.DataFrame, dict[str, Any]], plan: dict[str, Any]) -> Result:
    frame, kinds = table
    checked, _ = validate_plan(plan_from_dict(plan), kinds)
    return run_plan(checked, frame, kinds)


def column(result: Result, name: str) -> list[Any]:
    index = [c["name"] for c in result.columns].index(name)
    return [row[index] for row in result.rows]


# ---------------------------------------------------------------- aggregate


def test_sum_by_group(table) -> None:  # type: ignore[no-untyped-def]
    result = run(table, {"op": "aggregate", "agg": "sum", "column": "revenue", "by": ["region"]})
    assert result.rows == [["East", 100.0], ["North", 60.0], ["South", 40.0], ["West", 15.0]]
    assert result.chart is not None and result.chart["type"] == "bar"
    assert "highest for East (100)" in result.summary and "lowest for West (15)" in result.summary


def test_count_and_filters(table) -> None:  # type: ignore[no-untyped-def]
    assert run(table, {"op": "aggregate", "agg": "count"}).rows == [[7]]
    filtered = run(
        table,
        {
            "op": "aggregate",
            "agg": "count",
            "filters": [{"column": "region", "op": "in", "value": ["north", "SOUTH"]}],
        },
    )
    assert filtered.rows == [[4]] and filtered.rows_used == 4 and filtered.rows_total == 7
    assert filtered.chart is None and filtered.chart_note == "A single number doesn't need a chart."


def test_mean_per_month_is_a_line_in_order(table) -> None:  # type: ignore[no-untyped-def]
    result = run(table, {"op": "aggregate", "agg": "mean", "column": "revenue", "period": "month"})
    assert column(result, "month") == ["2024-01", "2024-02", "2024-03"]
    assert column(result, "mean of revenue") == pytest.approx([30.0, 20.0, 115 / 3])
    assert result.chart is not None and result.chart["type"] == "line"
    assert result.chart["x_kind"] == "time"
    assert (
        "Used 'date' for the dates."
        in validate_plan(
            plan_from_dict({"op": "aggregate", "agg": "count", "period": "month"}), table[1]
        )[1]
    )


def test_other_aggregates(table) -> None:  # type: ignore[no-untyped-def]
    assert run(table, {"op": "aggregate", "agg": "distinct", "column": "channel"}).rows == [[2]]
    assert run(table, {"op": "aggregate", "agg": "median", "column": "units"}).rows == [[3.5]]
    assert run(table, {"op": "aggregate", "agg": "count", "column": "units"}).rows == [[6]]
    latest = run(table, {"op": "aggregate", "agg": "max", "column": "date", "by": ["region"]})
    assert {r[0]: r[1] for r in latest.rows}["East"] == "2024-03-15"
    assert latest.chart is None  # dates are shown as a table
    two = run(
        table,
        {"op": "aggregate", "agg": "sum", "column": "revenue", "by": ["region", "channel"]},
    )
    assert two.chart is not None and two.chart["series"] == "channel"
    quarter = run(
        table, {"op": "aggregate", "agg": "sum", "column": "revenue", "period": "quarter"}
    )
    assert quarter.rows == [["2024 Q1", 215.0]]


def test_limit_and_sort(table) -> None:  # type: ignore[no-untyped-def]
    result = run(
        table,
        {
            "op": "aggregate",
            "agg": "sum",
            "column": "revenue",
            "by": ["region"],
            "sort": "value_asc",
            "limit": 2,
        },
    )
    assert result.rows == [["West", 15.0], ["South", 40.0]]
    assert "Showing 2 of 4 groups." in result.notes


# ---------------------------------------------------------------- the other operations


def test_compare_two_groups_uses_welch_and_ranks(table) -> None:  # type: ignore[no-untyped-def]
    result = run(table, {"op": "compare_groups", "column": "revenue", "group": "channel"})
    expected = stats.welch_t([20, 10, 30, 15], [40, 50, 50])
    assert result.stats["t"] == pytest.approx(expected.t)
    assert result.stats["p"] == pytest.approx(expected.p)
    assert result.stats["rank_test"] == "Mann–Whitney U"
    assert column(result, "rows") == [4, 3]
    assert "Online averages 18.75 and Store 46.6667" in result.summary


def test_compare_many_groups_uses_anova(table) -> None:  # type: ignore[no-untyped-def]
    result = run(
        table,
        {
            "op": "compare_groups",
            "column": "revenue",
            "group": "region",
            "groups": ["North", "South", "East"],
        },
    )
    expected = stats.one_way_anova([[50, 50], [20, 40], [10, 30]])
    assert result.stats["test"] == "one-way ANOVA"
    assert result.stats["f"] == pytest.approx(expected.f)
    with pytest.raises(PlanError, match="t-test compares two groups"):
        run(
            table,
            {"op": "compare_groups", "column": "revenue", "group": "region", "test": "t_test"},
        )


def test_compare_categories_uses_chi_square(table) -> None:  # type: ignore[no-untyped-def]
    result = run(table, {"op": "compare_groups", "column": "returned", "group": "channel"})
    assert result.stats["test"] == "chi-square"
    assert "below 5" in " ".join(result.notes)  # tiny table: the result is rough


def test_relationship(table) -> None:  # type: ignore[no-untyped-def]
    result = run(table, {"op": "relationship", "x": "units", "y": "revenue", "color": "region"})
    assert result.stats["r"] == pytest.approx(1.0)
    assert result.stats["slope"] == pytest.approx(10.0)
    assert result.stats["n"] == 6  # the row without units is left out
    assert result.chart is not None and result.chart["type"] == "scatter"
    assert "a strong positive relationship" in result.summary


def test_correlation(table) -> None:  # type: ignore[no-untyped-def]
    result = run(table, {"op": "correlation"})
    assert result.rows == [["units", "revenue", pytest.approx(1.0)]]
    assert result.chart is not None and result.chart["type"] == "heatmap"


def test_distribution(table) -> None:  # type: ignore[no-untyped-def]
    numbers = run(table, {"op": "distribution", "column": "revenue", "bins": 2})
    assert sum(column(numbers, "rows")) == 7
    assert numbers.stats["median"] == 30.0
    assert numbers.chart is not None and numbers.chart["type"] == "histogram"
    words = run(table, {"op": "distribution", "column": "channel"})
    assert words.rows == [["Online", 4], ["Store", 3]]
    dates = run(table, {"op": "distribution", "column": "date"})
    assert dates.rows == [["2024-01", 2], ["2024-02", 2], ["2024-03", 3]]


def test_crosstab(table) -> None:  # type: ignore[no-untyped-def]
    result = run(table, {"op": "crosstab", "rows": "region", "columns": "channel"})
    as_dict = {r[0]: r[1:] for r in result.rows}
    assert as_dict["East"] == [0, 2] and as_dict["South"] == [2, 0]
    shares = run(
        table, {"op": "crosstab", "rows": "region", "columns": "channel", "normalize": "rows"}
    )
    assert {r[0]: r[1:] for r in shares.rows}["North"] == [0.5, 0.5]


def test_outliers() -> None:
    raw = pd.DataFrame({"v": [str(x) for x in [10, 11, 12, 11, 10, 12, 11, 95]]})
    frame, kinds = typed_frame(raw)
    plan, _ = validate_plan(plan_from_dict({"op": "outliers", "column": "v"}), kinds)
    result = run_plan(plan, frame, kinds)
    assert result.stats["outliers"] == 1
    assert result.rows[0][:2] == [8, 95.0]  # row 8 of the file
    z, _ = validate_plan(
        plan_from_dict({"op": "outliers", "column": "v", "method": "zscore", "threshold": 2}), kinds
    )
    assert run_plan(z, frame, kinds).stats["outliers"] == 1


def test_missing_summary_and_rows(table) -> None:  # type: ignore[no-untyped-def]
    missing = run(table, {"op": "missing"})
    assert missing.rows[0][:2] == ["note", 5] and missing.rows[1][:2] == ["units", 1]
    summary = run(table, {"op": "summary"})
    assert len(summary.rows) == 7 and summary.summary.startswith("The table has 7 rows")
    rows = run(
        table,
        {
            "op": "rows",
            "sort_by": "revenue",
            "descending": True,
            "limit": 2,
            "columns": ["region", "revenue"],
        },
    )
    assert rows.rows == [[5, "East", 50.0], [6, "East", 50.0]]


@pytest.mark.parametrize(
    ("flt", "count"),
    [
        ({"column": "date", "op": "between", "value": ["2024-02-01", "2024-03-01"]}, 3),
        ({"column": "date", "op": "eq", "value": "2024-03"}, 3),
        ({"column": "note", "op": "contains", "value": "GIFT"}, 2),
        ({"column": "note", "op": "is_null"}, 5),
        ({"column": "units", "op": "not_null"}, 6),
        ({"column": "units", "op": "gte", "value": "4"}, 3),
        ({"column": "revenue", "op": "lt", "value": 20}, 2),
        ({"column": "region", "op": "ne", "value": "north"}, 5),
        ({"column": "region", "op": "not_in", "value": ["East", "West"]}, 4),
        ({"column": "returned", "op": "eq", "value": True}, 1),
        ({"column": "returned", "op": "eq", "value": "no"}, 6),
    ],
)
def test_filters(table, flt: dict[str, Any], count: int) -> None:  # type: ignore[no-untyped-def]
    result = run(table, {"op": "aggregate", "agg": "count", "filters": [flt]})
    assert result.rows == [[count]]


def test_no_rows_left(table) -> None:  # type: ignore[no-untyped-def]
    result = run(
        table,
        {
            "op": "aggregate",
            "agg": "sum",
            "column": "revenue",
            "by": ["region"],
            "filters": [{"column": "region", "op": "eq", "value": "Mars"}],
        },
    )
    assert result.rows == [] and "nothing to compute" in result.summary


# ---------------------------------------------------------------- validation


def problems(table, plan: dict[str, Any]) -> list[str]:  # type: ignore[no-untyped-def]
    with pytest.raises(PlanError) as err:
        validate_plan(plan_from_dict(plan), table[1])
    return err.value.problems


def test_unknown_column_suggests_the_closest(table) -> None:  # type: ignore[no-untyped-def]
    assert problems(table, {"op": "aggregate", "agg": "count", "by": ["regoin"]}) == [
        "There's no column 'regoin'. Did you mean 'region'?"
    ]


def test_harmless_differences_are_fixed(table) -> None:  # type: ignore[no-untyped-def]
    plan, _ = validate_plan(
        plan_from_dict({"op": "aggregate", "agg": "sum", "column": " Revenue ", "by": ["REGION"]}),
        table[1],
    )
    assert plan.column == "revenue" and plan.by == ["region"]  # type: ignore[union-attr]


def test_type_errors_are_plain(table) -> None:  # type: ignore[no-untyped-def]
    assert problems(table, {"op": "aggregate", "agg": "sum", "column": "region"}) == [
        "'region' holds text, but 'sum' needs a number column."
    ]
    text_compare = problems(
        table, {"op": "rows", "filters": [{"column": "region", "op": "gt", "value": 3}]}
    )
    assert "doesn't apply" in text_compare[0]
    assert (
        "needs numbers"
        in problems(
            table, {"op": "rows", "filters": [{"column": "units", "op": "gt", "value": "lots"}]}
        )[0]
    )


def test_period_needs_a_date_column() -> None:
    _frame, kinds = typed_frame(pd.DataFrame({"a": ["1", "2"]}))
    with pytest.raises(PlanError, match="needs a date column"):
        validate_plan(plan_from_dict({"op": "aggregate", "agg": "count", "period": "month"}), kinds)


def test_off_menu_plans_are_refused() -> None:
    with pytest.raises(PlanError, match="isn't on the menu"):
        plan_from_dict({"op": "eval", "code": "1+1"})
    with pytest.raises(PlanError) as err:
        plan_from_dict({"op": "aggregate", "agg": "count", "code": "__import__('os')"})
    assert any("isn't allowed" in p for p in err.value.problems)
    with pytest.raises(PlanError):
        plan_from_dict({"op": "aggregate", "agg": "sum"})  # sum needs a column
    with pytest.raises(PlanError):
        plan_from_dict({"op": "rows", "filters": [{"column": "a", "op": "between", "value": 3}]})
    with pytest.raises(NoPlan):
        plan_from_dict({"op": "none"})


def test_parse_plan_from_a_chatty_reply() -> None:
    plan = parse_plan('Sure! ```json\n{"op": "aggregate", "agg": "count", "by": ["a"]}\n``` Done.')
    assert plan.op == "aggregate"
    with pytest.raises(PlanError, match="wasn't a JSON object"):
        parse_plan("I think you should sum the revenue.")


# ---------------------------------------------------------------- code injection


def test_code_in_names_and_values_is_never_run(
    table, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    marker = tmp_path / "pwned"
    attack = f"__import__('os').system('touch {marker}')"

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("query/eval must never be used")

    monkeypatch.setattr(pd.DataFrame, "query", boom)
    monkeypatch.setattr(pd.DataFrame, "eval", boom)
    monkeypatch.setattr(pd, "eval", boom)
    with pytest.raises(PlanError):
        run(table, {"op": "aggregate", "agg": "sum", "column": attack})
    for value in (attack, "@x or 1==1", "`region` == region", "North' or '1'=='1"):
        result = run(
            table,
            {
                "op": "aggregate",
                "agg": "count",
                "filters": [{"column": "region", "op": "eq", "value": value}],
            },
        )
        assert result.rows == [[0]]
        contains = run(
            table, {"op": "rows", "filters": [{"column": "note", "op": "contains", "value": value}]}
        )
        assert contains.rows == []
    for plan in (
        {"op": "rows", "sort_by": attack},
        {"op": "crosstab", "rows": "region", "columns": attack},
        {"op": "relationship", "x": "units", "y": "revenue", "color": "__class__"},
    ):
        with pytest.raises(PlanError):
            run(table, plan)
    assert not marker.exists()


def test_analysis_code_never_evaluates_text() -> None:
    package = Path(__file__).resolve().parents[1] / "outlawz" / "analysis"
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in (
            r"(?<![\w.])eval\(",
            r"(?<![\w.])exec\(",
            r"(?<![\w.])compile\(",
            r"\.query\(",
            r"\.eval\(",
            r"__import__",
        ):
            assert not re.search(forbidden, source), f"{path.name} matches {forbidden}"


# ---------------------------------------------------------------- charts


def spec(**kwargs: Any) -> ChartSpec:
    base: dict[str, Any] = {"type": "bar", "title": "t", "x": "g", "y": "v"}
    return ChartSpec(**{**base, **kwargs})


def test_line_needs_an_ordered_x() -> None:
    data = [{"g": "b", "v": 1}, {"g": "a", "v": 2}]
    with pytest.raises(ChartError, match="ordered"):
        validate_chart(spec(type="line", x_kind="category", data=data))
    fitted, _ = fit_chart(spec(type="line", x_kind="category", data=data))
    assert fitted is not None and fitted.type == "bar"
    assert "no natural order" in fitted.notes[0]
    time = spec(
        type="line", x_kind="time", data=[{"g": "2024-02", "v": 1}, {"g": "2024-01", "v": 2}]
    )
    fitted_time, _ = fit_chart(time)
    assert fitted_time is not None and [r["g"] for r in fitted_time.data] == ["2024-01", "2024-02"]


def test_too_many_bars() -> None:
    data = [{"g": f"k{i}", "v": i} for i in range(87)]
    fitted, _ = fit_chart(spec(data=data))
    assert fitted is not None and len(fitted.data) == 40
    assert "Showing the 40 largest of 87 groups." in fitted.notes
    assert min(r["v"] for r in fitted.data) == 47
    ordered = [{"g": f"2024-{i:03d}", "v": i} for i in range(60)]
    as_line, _ = fit_chart(spec(data=ordered, x_kind="time"))
    assert as_line is not None and as_line.type == "line"


def test_series_and_colour_limits() -> None:
    data = [{"g": "a", "s": f"s{i}", "v": i} for i in range(12)]
    fitted, _ = fit_chart(spec(series="s", data=data))
    assert fitted is not None and len({r["s"] for r in fitted.data}) == 8
    points = [{"x": i, "y": i * 2, "c": f"c{i % 6}"} for i in range(60)]
    scatter, _ = fit_chart(
        ChartSpec(type="scatter", title="t", x="x", y="y", series="c", x_kind="number", data=points)
    )
    assert scatter is not None and len({r["c"] for r in scatter.data}) == 4  # 3 + Other
    assert "Other" in {r["c"] for r in scatter.data}


def test_heatmap_limit() -> None:
    data = [{"a": f"a{i}", "b": f"b{j}", "n": i + j} for i in range(30) for j in range(30)]
    fitted, _ = fit_chart(ChartSpec(type="heatmap", title="t", x="a", y="b", value="n", data=data))
    assert fitted is not None
    assert len({r["a"] for r in fitted.data}) == 25 and len({r["b"] for r in fitted.data}) == 25


def test_bad_specs_are_refused() -> None:
    with pytest.raises(ChartError, match="needs the field"):
        validate_chart(spec(data=[{"g": "a"}]))
    with pytest.raises(ChartError, match="must hold numbers"):
        validate_chart(spec(data=[{"g": "a", "v": "lots"}]))
    with pytest.raises(ChartError, match="Only heatmaps"):
        validate_chart(spec(value="v", data=[{"g": "a", "v": 1}]))
    assert fit_chart(spec(data=[])) == (None, "There's nothing to plot after the filters.")


# ---------------------------------------------------------------- numbers check


def test_numbers_in_text() -> None:
    found = numbers_in("Sales were 1,234.5 in 2024 [2], up 12% and −3 elsewhere; Q3 was best.")
    assert [(f.value, f.decimals, f.percent) for f in found] == [
        (1234.5, 1, False),
        (2024.0, 0, False),
        (12.0, 0, True),
        (-3.0, 0, False),
    ]


def test_numbers_check() -> None:
    computed = [1234.5678, 0.4512, 2024, 7]
    assert unmatched_numbers("Revenue was 1,234.57 in 2024 across 7 rows.", computed) == []
    assert unmatched_numbers("Revenue was about 1,235.", computed) == []
    assert unmatched_numbers("45% were returned.", computed) == []
    assert unmatched_numbers("45.1% were returned.", computed) == []
    assert unmatched_numbers("Revenue was 1,300 and 12 returns [1].", computed) == ["1,300", "12"]


def test_result_numbers_include_table_stats_and_filters(table) -> None:  # type: ignore[no-untyped-def]
    result = run(
        table,
        {
            "op": "aggregate",
            "agg": "sum",
            "column": "revenue",
            "by": ["region"],
            "filters": [{"column": "units", "op": "gte", "value": 2}],
        },
    )
    numbers = result.numbers()
    assert 100.0 in numbers and 2.0 in numbers and 7.0 in numbers


# ---------------------------------------------------------------- overview and Compare


def sample(name: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw, _ = load_csv(SAMPLES / name)
    return typed_frame(raw)


def test_overview_of_the_sample() -> None:
    frame, kinds = sample("lamp_sales_2024.csv")
    data = overview(frame, kinds)
    assert data["rows"] == 723 and data["duplicates"] == 3
    kinds_found = {f["kind"] for f in data["findings"]}
    assert {"duplicates", "outliers"} <= kinds_found
    units = next(c for c in data["cards"] if c["name"] == "units")
    assert units["kind"] == "number" and sum(b["rows"] for b in units["histogram"]) == 723
    region = next(c for c in data["cards"] if c["name"] == "region")
    assert region["top"][0]["value"] == "North"
    assert data["correlations"][0]["r"] > 0.5
    assert data["suggestions"] and all("plan" in s for s in data["suggestions"])
    for suggestion in data["suggestions"]:  # every suggestion is a plan that runs
        plan, _ = validate_plan(plan_from_dict(suggestion["plan"]), kinds)
        run_plan(plan, frame, kinds)


def test_compare_the_two_samples() -> None:
    a, ka = sample("lamp_sales_2024.csv")
    b, kb = sample("lamp_sales_2025.csv")
    data = compare_frames(a, ka, b, kb, name_a="2024", name_b="2025")
    assert data["added"] == ["coupon"] and data["removed"] == ["discount_pct"]
    assert {"column": "rating", "from": "number", "to": "text"} in data["type_changes"]
    cats = {c["column"]: c for c in data["categories"]}
    assert cats["region"]["new"] == ["Central"]
    assert cats["product"]["new"] == ["Reading Lamp"] and cats["product"]["vanished"] == [
        "Smart Plug"
    ]
    price = next(n for n in data["numbers"] if n["column"] == "unit_price")
    assert price["shift"] > 0
    assert data["summary"].startswith("2024 has 723 rows and 2025 has 763.")
