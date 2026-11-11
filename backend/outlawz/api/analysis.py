"""Data analysis for CSVs: the fixed menu, the automatic overview, running a plan (the builder,
no model involved), asking a question (model → plan → computed answer), and comparing two CSVs.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, get_args

from fastapi import APIRouter
from pydantic import BaseModel, Field

from outlawz.analysis.compare import compare_frames
from outlawz.analysis.plans import (
    FILTER_HELP,
    MENU,
    Agg,
    FilterOp,
    Period,
    PlanError,
    plan_from_dict,
)
from outlawz.analysis.profile import overview
from outlawz.api.deps import State
from outlawz.errors import AppError
from outlawz.llm.base import LLMError

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


class RunBody(BaseModel):
    doc_id: str = Field(max_length=64)
    plan: dict[str, Any]


class AskBody(BaseModel):
    doc_id: str = Field(max_length=64)
    question: str = Field(min_length=1, max_length=2000)


class CompareBody(BaseModel):
    a: str = Field(max_length=64)
    b: str = Field(max_length=64)


def _plan_error(err: PlanError) -> AppError:
    return AppError("invalid_plan", " ".join(err.problems), 422, problems=err.problems)


def _observe(state: Any, kind: str, ms: float) -> None:
    hook = getattr(state, "on_analysis_step", None)
    if hook is not None:
        hook(kind, ms)


@router.get("/menu")
def menu() -> dict[str, Any]:
    return {
        "ops": MENU,
        "aggs": list(get_args(Agg)),
        "periods": list(get_args(Period)),
        "filter_ops": list(get_args(FilterOp)),
        "filters": FILTER_HELP,
    }


@router.get("/{doc_id}/overview")
async def table_overview(doc_id: str, state: State) -> dict[str, Any]:
    analyst = state.analyst
    doc = analyst.csv_document(doc_id)
    started = time.perf_counter()
    frame, kinds = await analyst.load_async(doc)
    data = await asyncio.to_thread(overview, frame, kinds)
    _observe(state, "overview", (time.perf_counter() - started) * 1000)
    return {
        "doc_id": doc.id,
        "document": doc.name,
        "columns": [{"name": c, "kind": kinds[c]} for c in frame.columns],
        "overview": data,
    }


@router.post("/run")
async def run(body: RunBody, state: State) -> dict[str, Any]:
    analyst = state.analyst
    doc = analyst.csv_document(body.doc_id)
    try:
        plan = plan_from_dict(body.plan)
    except PlanError as err:
        raise _plan_error(err) from None
    except Exception:
        raise AppError("invalid_plan", "That plan isn't on the menu.", 422) from None
    started = time.perf_counter()
    try:
        result, fixes = await asyncio.to_thread(analyst.run, doc, plan)
    except PlanError as err:
        raise _plan_error(err) from None
    _observe(state, "run", (time.perf_counter() - started) * 1000)
    state.events.emit("analysis_run", doc_id=doc.id, op=plan.op)
    return {"doc_id": doc.id, "document": doc.name, "result": result.to_dict(), "fixes": fixes}


@router.post("/ask")
async def ask(body: AskBody, state: State) -> dict[str, Any]:
    analyst = state.analyst
    doc = analyst.csv_document(body.doc_id)
    try:
        answer = await analyst.ask(doc, body.question)
    except LLMError as exc:
        raise AppError(exc.code, exc.message, 502) from None
    if answer is None:
        raise AppError(
            "no_plan",
            "This question doesn't fit the table's columns. Try naming a column, or use the "
            "builder to choose an analysis.",
            422,
        )
    return {"doc_id": doc.id, "document": doc.name, **answer.to_dict()}


@router.post("/compare")
async def compare(body: CompareBody, state: State) -> dict[str, Any]:
    analyst = state.analyst
    doc_a, doc_b = analyst.csv_document(body.a), analyst.csv_document(body.b)
    if doc_a.id == doc_b.id:
        raise AppError("same_table", "Choose two different CSV files to compare.", 422)
    (fa, ka), (fb, kb) = await asyncio.gather(analyst.load_async(doc_a), analyst.load_async(doc_b))
    data = await asyncio.to_thread(
        compare_frames, fa, ka, fb, kb, name_a=doc_a.name, name_b=doc_b.name
    )
    return {"a": doc_a.id, "b": doc_b.id, **data}
