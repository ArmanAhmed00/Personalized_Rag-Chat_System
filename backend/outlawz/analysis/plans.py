"""The fixed menu of analysis plans.

The model never writes or runs code. For a data question it picks one plan from this menu as JSON;
the server validates it against the real columns and runs a fixed pandas routine. Every model here
forbids extra fields, so anything off the menu is refused before it gets near the data.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

Agg = Literal["count", "sum", "mean", "median", "min", "max", "distinct", "std"]
Period = Literal["day", "week", "month", "quarter", "year"]
FilterOp = Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "contains",
    "between",
    "is_null",
    "not_null",
]

Scalar = str | int | float | bool
MAX_NAME = 200


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Filter(Strict):
    column: str = Field(max_length=MAX_NAME)
    op: FilterOp
    value: Scalar | list[Scalar] | None = None

    @model_validator(mode="after")
    def _shape(self) -> Filter:
        op, value = self.op, self.value
        if op in ("is_null", "not_null"):
            if value not in (None, [], ""):
                raise ValueError(f"'{op}' takes no value")
            self.value = None
        elif op in ("in", "not_in"):
            if not isinstance(value, list) or not value or len(value) > 200:
                raise ValueError(f"'{op}' needs a list of 1 to 200 values")
        elif op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("'between' needs a list of two values: [low, high]")
        elif value is None or isinstance(value, list):
            raise ValueError(f"'{op}' needs a single value")
        if isinstance(value, str) and len(value) > 500:
            raise ValueError("filter values must be under 500 characters")
        return self


class PlanBase(Strict):
    filters: list[Filter] = Field(default_factory=list, max_length=10)


class Aggregate(PlanBase):
    op: Literal["aggregate"]
    agg: Agg
    column: str | None = Field(None, max_length=MAX_NAME)
    by: list[str] = Field(default_factory=list, max_length=2)
    period: Period | None = None
    date_column: str | None = Field(None, max_length=MAX_NAME)
    sort: Literal["value_desc", "value_asc", "label"] = "value_desc"
    limit: int = Field(100, ge=1, le=1000)

    @model_validator(mode="after")
    def _needs_column(self) -> Aggregate:
        if self.agg != "count" and not self.column:
            raise ValueError(f"'{self.agg}' needs a column")
        if self.period and len(self.by) > 1:
            raise ValueError("with a period, group by at most one other column")
        return self


class CompareGroups(PlanBase):
    op: Literal["compare_groups"]
    column: str = Field(max_length=MAX_NAME)
    group: str = Field(max_length=MAX_NAME)
    test: Literal["auto", "t_test", "anova", "chi_square"] = "auto"
    groups: list[Scalar] | None = Field(None, max_length=50)


class Relationship(PlanBase):
    op: Literal["relationship"]
    x: str = Field(max_length=MAX_NAME)
    y: str = Field(max_length=MAX_NAME)
    color: str | None = Field(None, max_length=MAX_NAME)


class Correlation(PlanBase):
    op: Literal["correlation"]
    columns: list[str] | None = Field(None, max_length=25)
    method: Literal["pearson", "spearman"] = "pearson"


class Distribution(PlanBase):
    op: Literal["distribution"]
    column: str = Field(max_length=MAX_NAME)
    bins: int = Field(20, ge=2, le=60)


class Crosstab(PlanBase):
    op: Literal["crosstab"]
    rows: str = Field(max_length=MAX_NAME)
    columns: str = Field(max_length=MAX_NAME)
    normalize: Literal["none", "rows", "columns", "all"] = "none"


class Outliers(PlanBase):
    op: Literal["outliers"]
    column: str = Field(max_length=MAX_NAME)
    method: Literal["iqr", "zscore"] = "iqr"
    threshold: float | None = Field(None, gt=0, le=10)
    limit: int = Field(50, ge=1, le=500)


class Missing(PlanBase):
    op: Literal["missing"]


class Summary(PlanBase):
    op: Literal["summary"]
    columns: list[str] | None = Field(None, max_length=50)


class Rows(PlanBase):
    op: Literal["rows"]
    columns: list[str] | None = Field(None, max_length=50)
    sort_by: str | None = Field(None, max_length=MAX_NAME)
    descending: bool = False
    limit: int = Field(20, ge=1, le=200)


Plan = Annotated[
    Aggregate
    | CompareGroups
    | Relationship
    | Correlation
    | Distribution
    | Crosstab
    | Outliers
    | Missing
    | Summary
    | Rows,
    Field(discriminator="op"),
]
PLAN_ADAPTER: TypeAdapter[Plan] = TypeAdapter(Plan)
OPS = (
    "aggregate",
    "compare_groups",
    "relationship",
    "correlation",
    "distribution",
    "crosstab",
    "outliers",
    "missing",
    "summary",
    "rows",
)


class PlanError(Exception):
    """A plan that can't run. ``problems`` are plain sentences, safe to show and to send back
    to the model."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class NoPlan(Exception):
    """The model said the question doesn't fit the table (``{"op": "none"}``)."""


def plan_from_dict(data: Any) -> Plan:
    if not isinstance(data, dict):
        raise PlanError(["The plan must be a JSON object."])
    if data.get("op") == "none":
        raise NoPlan()
    if data.get("op") not in OPS:
        raise PlanError(
            [f"'{str(data.get('op'))[:40]}' isn't on the menu. Use one of: {', '.join(OPS)}."]
        )
    try:
        return PLAN_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise PlanError(_problems(exc)) from None


def _problems(exc: ValidationError) -> list[str]:
    problems = []
    for err in exc.errors()[:6]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if not str(p).startswith("function-"))
        # The discriminator name ("aggregate") leads the location; drop it.
        loc = re.sub(r"^[a-z_]+\.?", "", loc, count=1) if loc.split(".")[0] in OPS else loc
        message = str(err.get("msg", "invalid")).removeprefix("Value error, ")
        if err.get("type") == "extra_forbidden":
            message = "isn't allowed in this plan"
        problems.append(f"{loc}: {message}" if loc else message)
    return problems


def parse_plan(text: str) -> Plan:
    """Read the model's reply: the first JSON object in it, validated against the menu."""
    obj = extract_json(text)
    if obj is None:
        raise PlanError(["The reply wasn't a JSON object."])
    return plan_from_dict(obj)


def extract_json(text: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        in_string = escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start : i + 1])
                    except ValueError:
                        break
        start = cleaned.find("{", start + 1)
    return None


MENU = {
    "aggregate": {
        "fields": "agg (count|sum|mean|median|min|max|distinct|std), column (not needed for "
        "count), by (up to 2 columns), period (day|week|month|quarter|year) with date_column, "
        "sort (value_desc|value_asc|label), limit",
        "example": {"op": "aggregate", "agg": "sum", "column": "revenue", "by": ["region"]},
    },
    "compare_groups": {
        "fields": "column (the values), group (the column that splits them), test "
        "(auto|t_test|anova|chi_square), groups (optional list of group values to keep)",
        "example": {"op": "compare_groups", "column": "revenue", "group": "channel"},
    },
    "relationship": {
        "fields": "x, y (two number columns), color (optional category column)",
        "example": {"op": "relationship", "x": "units", "y": "revenue"},
    },
    "correlation": {
        "fields": "columns (optional list of number columns), method (pearson|spearman)",
        "example": {"op": "correlation"},
    },
    "distribution": {
        "fields": "column, bins",
        "example": {"op": "distribution", "column": "revenue"},
    },
    "crosstab": {
        "fields": "rows, columns (two category columns), normalize (none|rows|columns|all)",
        "example": {"op": "crosstab", "rows": "region", "columns": "channel"},
    },
    "outliers": {
        "fields": "column, method (iqr|zscore), threshold (optional), limit",
        "example": {"op": "outliers", "column": "revenue"},
    },
    "missing": {"fields": "(none)", "example": {"op": "missing"}},
    "summary": {"fields": "columns (optional)", "example": {"op": "summary"}},
    "rows": {
        "fields": "columns (optional), sort_by, descending, limit (max 200)",
        "example": {"op": "rows", "sort_by": "revenue", "descending": True, "limit": 5},
    },
}

FILTER_HELP = (
    "Every plan may have filters: a list of {column, op, value}. op is one of eq, ne, gt, gte, "
    "lt, lte, in, not_in (value is a list), contains, between (value is [low, high]), is_null, "
    "not_null (no value)."
)


def menu_text() -> str:
    lines = []
    for op, info in MENU.items():
        lines.append(f"- {op}: {info['fields']}. Example: {json.dumps(info['example'])}")
    return "\n".join(lines) + "\n" + FILTER_HELP
