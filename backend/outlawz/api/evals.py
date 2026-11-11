"""Evaluations: start a run, follow its progress, read saved runs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from outlawz.api.deps import State
from outlawz.errors import AppError, NotFound
from outlawz.evals.runner import SUITES

router = APIRouter(prefix="/api/evals", tags=["evaluations"])


class Start(BaseModel):
    suites: list[str] | None = Field(None, max_length=len(SUITES))


def _summary(run: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "status", "started", "finished", "seconds", "suites", "progress", "error")
    out = {k: run[k] for k in keep if k in run}
    changes = run.get("changes") or []
    out["better"] = sum(1 for c in changes if c.get("better"))
    out["worse"] = sum(1 for c in changes if not c.get("better"))
    return out


@router.get("")
def list_runs(state: State) -> dict[str, Any]:
    active = [_summary(r) for r in state.evals.active.values()]
    return {"runs": active + [_summary(r) for r in state.evals.runs()], "suites": list(SUITES)}


@router.post("", status_code=202)
async def start_run(body: Start, state: State) -> dict[str, Any]:
    if state.evals.active:
        raise AppError("busy", "An evaluation is already running. Wait for it to finish.", 409)
    chosen = body.suites
    if chosen is not None:
        unknown = set(chosen) - set(SUITES)
        if unknown or not chosen:
            raise AppError("invalid_request", f"Choose suites from: {', '.join(SUITES)}.", 422)
    run_id = state.evals.start(chosen)
    return {"id": run_id, "status": "running"}


@router.get("/{run_id}")
def get_run(run_id: str, state: State) -> dict[str, Any]:
    run = state.evals.get(run_id)
    if run is None:
        raise NotFound("That evaluation run")
    return run
