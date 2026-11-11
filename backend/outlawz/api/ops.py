"""System pages: overview, metrics, processing, traces, the activity log, and the update notice."""

from __future__ import annotations

import shutil
import time
from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import Response

from outlawz import __version__
from outlawz.api.deps import State
from outlawz.errors import AppError, NotFound
from outlawz.library.documents import Status
from outlawz.obs.activity import ACTIONS
from outlawz.obs.daily import window_seconds
from outlawz.obs.metrics import percentiles

router = APIRouter(prefix="/api/system", tags=["system pages"])
STARTED = time.time()


def _obs(state: Any) -> Any:
    if state.obs is None:
        raise AppError("unavailable", "The system pages aren't available right now.", 503)
    return state.obs


def _folder_size(path: Any) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


@router.get("/overview")
def overview(state: State) -> dict[str, Any]:
    obs = _obs(state)
    docs = state.docs.all()
    by_status = Counter(d.status.value for d in docs)
    by_type = Counter(d.type for d in docs)
    day = obs.metrics.summary(24 * 3600)
    try:
        disk = shutil.disk_usage(state.lib.root)
        free = {"free_bytes": disk.free, "total_bytes": disk.total}
    except OSError:
        free = {}
    last = obs.activity.query(limit=1)
    return {
        "version": __version__,
        "uptime_s": round(time.time() - STARTED),
        "library": str(state.settings.library),
        "documents": {
            "total": len(docs),
            "by_status": dict(by_status),
            "by_type": dict(by_type),
            "passages": state.index.passage_count,
            "bytes": sum(d.size for d in docs),
        },
        "chats": len(list(state.lib.chats_dir.glob("*.json"))),
        "queue": {
            "waiting": state.queue.length if state.queue else 0,
            "reading": len(state.queue.current) if state.queue else 0,
        },
        "model": {
            "llm": state.settings.llm,
            "llm_model": state.settings.llm_model,
            "embed_model": state.settings.embed_model,
            "cloud": state.settings.is_cloud_llm,
        },
        "last_24h": {
            "requests": day["requests"],
            "errors": day["errors"],
            "p95_ms": day["latency"]["p95"],
        },
        "storage": {
            "app_folder_bytes": _folder_size(state.lib.app),
            "logs_bytes": _folder_size(state.lib.logs_dir),
            **free,
        },
        "activity": {"events": last[0]["seq"] if last else 0},
    }


@router.get("/metrics")
def metrics(state: State, window: str = Query("24h")) -> dict[str, Any]:
    obs = _obs(state)
    data: dict[str, Any] = obs.metrics.summary(window_seconds(window))
    data["window"] = window
    return data


@router.get("/processing")
def processing(state: State) -> dict[str, Any]:
    obs = _obs(state)
    docs = state.docs.all()
    queue = state.queue
    now = time.time()
    current = []
    if queue is not None:
        for doc_id, started in sorted(queue.current.items(), key=lambda kv: kv[1]):
            doc = state.docs.get(doc_id)
            if doc is not None:
                current.append(
                    {
                        "id": doc.id,
                        "name": doc.name,
                        "seconds": round(now - started, 1),
                        "status": doc.status.value,
                        "progress": doc.progress.model_dump(),
                    }
                )
    waiting = [
        {"id": d.id, "name": d.name, "status": d.status.value, "message": d.progress.message}
        for d in docs
        if not d.status.settled and (queue is None or d.id not in queue.current)
    ]
    failures: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "documents": []})
    for d in docs:
        if d.status == Status.failed and d.error is not None:
            entry = failures[d.error.code]
            entry["count"] += 1
            entry["message"] = d.error.message
            entry["documents"].append({"id": d.id, "name": d.name})
    week = obs.metrics.records(now - 7 * 24 * 3600)
    per_doc = [
        float(r["ms"])
        for r in week
        if r.get("name") == "ingest.document" and r.get("outcome") == "ready"
    ]
    step_times = {
        step: percentiles([float(r["ms"]) for r in week if r.get("name") == f"ingest.{step}"])
        for step in ("extract", "chunk", "embed", "write")
    }
    return {
        "waiting": len(waiting),
        "queue": waiting,
        "current": current,
        "failures": [
            {"code": code, **info}
            for code, info in sorted(failures.items(), key=lambda kv: -kv[1]["count"])
        ],
        "time_per_document": {"count": len(per_doc), **percentiles(per_doc)},
        "steps": step_times,
        "workers": state.settings.workers,
    }


@router.get("/processing/{doc_id}")
def document_timeline(doc_id: str, state: State) -> dict[str, Any]:
    obs = _obs(state)
    doc = state.require_document(doc_id)
    since = time.time() - state.settings.log_days * 24 * 3600
    runs = [
        t
        for t in obs.tracer.recent(since, kind="ingest", limit=100_000)
        if (t.get("ids") or {}).get("doc_id") == doc_id
    ]
    return {
        "id": doc.id,
        "name": doc.name,
        "status": doc.status.value,
        "added_at": doc.added_at,
        "ready_at": doc.ready_at,
        "error": doc.error.model_dump() if doc.error else None,
        "runs": runs[:20],
    }


@router.get("/traces")
def traces(
    state: State,
    window: str = Query("24h"),
    kind: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    obs = _obs(state)
    since = time.time() - window_seconds(window)
    items = obs.tracer.recent(since, kind=kind, limit=limit)
    return {
        "window": window,
        "traces": items,
        "where_time_goes": obs.tracer.where_time_goes(since),
        "sample": state.settings.trace_sample,
        "slow_ms": state.settings.slow_trace_ms,
    }


@router.get("/traces/{trace_id}")
def trace(trace_id: str, state: State) -> dict[str, Any]:
    obs = _obs(state)
    found = obs.tracer.get(trace_id, time.time() - state.settings.log_days * 24 * 3600)
    if found is None:
        raise NotFound("That trace")
    result: dict[str, Any] = found
    return result


def _activity(
    state: Any, action: str | None, since: float | None, until: float | None, limit: int
) -> list[dict[str, Any]]:
    if action is not None and action not in ACTIONS:
        raise AppError(
            "invalid_request", f"Unknown action. Choose one of: {', '.join(ACTIONS)}.", 422
        )
    events: list[dict[str, Any]] = _obs(state).activity.query(
        action=action, since=since, until=until, limit=limit
    )
    return events


@router.get("/activity")
def activity(
    state: State,
    action: str | None = Query(None),
    since: float | None = Query(None),
    until: float | None = Query(None),
    limit: int = Query(200, ge=1, le=5000),
) -> dict[str, Any]:
    return {"events": _activity(state, action, since, until, limit), "actions": list(ACTIONS)}


@router.get("/activity/check")
def activity_check(state: State) -> dict[str, Any]:
    result: dict[str, Any] = _obs(state).activity.check()
    return result


@router.get("/activity.csv")
def activity_csv(
    state: State,
    action: str | None = Query(None),
    since: float | None = Query(None),
    until: float | None = Query(None),
) -> Response:
    events = _activity(state, action, since, until, 1_000_000)
    return Response(
        _obs(state).activity.to_csv(events),
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": 'attachment; filename="outlawz-activity.csv"'},
    )


_UPDATE_CACHE: dict[str, Any] = {}
UPDATE_URL = "https://pypi.org/pypi/outlawz/json"


def _newer(latest: str, current: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        out = []
        for piece in v.split(".")[:3]:
            digits = "".join(ch for ch in piece if ch.isdigit())
            out.append(int(digits or 0))
        return tuple(out)

    return parts(latest) > parts(current)


@router.get("/update")
async def update_notice(state: State) -> dict[str, Any]:
    """Checks PyPI for a newer version, only if the user turned this on. Cached for a day."""
    if not state.settings.update_check:
        return {"enabled": False}
    cached = _UPDATE_CACHE.get("result")
    if cached and time.time() - _UPDATE_CACHE.get("at", 0) < 24 * 3600:
        return dict(cached)
    import httpx

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(UPDATE_URL)
        latest = str(response.json()["info"]["version"]) if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError):
        latest = None
    result = {
        "enabled": True,
        "current": __version__,
        "latest": latest,
        "update": bool(latest and _newer(latest, __version__)),
        "checked": latest is not None,
    }
    _UPDATE_CACHE.update(result=result, at=time.time())
    return result
