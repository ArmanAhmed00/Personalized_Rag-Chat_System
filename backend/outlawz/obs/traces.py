"""Traces: a timed waterfall for each answer, search, document read and analysis, keyed by the
request id (or a new id for background work).

Errors and slow traces (over ``slow_trace_ms``) are always kept; the rest are sampled at
``trace_sample``. Like metrics, traces hold timings, ids and codes only.
"""

from __future__ import annotations

import random
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from outlawz.obs.daily import DailyLog

KINDS = ("answer", "search", "ingest", "analysis")


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def spans_in_order(parts: list[tuple[str, float | None]]) -> list[dict[str, Any]]:
    """Lay steps that ran one after another end to end: ``[("embed", 3.1), ("vector", 1.2)]``."""
    spans, cursor = [], 0.0
    for name, ms in parts:
        if ms is None or ms < 0:
            continue
        spans.append({"name": name, "start_ms": round(cursor, 2), "ms": round(float(ms), 2)})
        cursor += float(ms)
    return spans


class Tracer:
    def __init__(
        self,
        folder: Path,
        sample: Callable[[], float],
        slow_ms: Callable[[], int],
        rng: random.Random | None = None,
    ) -> None:
        self.log = DailyLog(folder, "traces")
        self.sample = sample
        self.slow_ms = slow_ms
        self.rng = rng or random.Random()
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(
        self,
        kind: str,
        total_ms: float,
        spans: list[dict[str, Any]],
        *,
        trace_id: str | None = None,
        status: str = "ok",
        code: str | None = None,
        ids: dict[str, str] | None = None,
        counts: dict[str, int | float] | None = None,
        always: bool = False,
    ) -> bool:
        """Keep the trace if it's an error, slow, picked by sampling, or ``always``. Returns
        whether it was kept."""
        slow = total_ms >= self.slow_ms()
        keep = always or status != "ok" or slow or self.rng.random() < self.sample()
        if not keep:
            return False
        trace = {
            "id": trace_id or new_trace_id(),
            "kind": kind,
            "t": round(time.time(), 3),
            "total_ms": round(total_ms, 2),
            "status": status,
            "slow": slow,
            "spans": spans,
        }
        if code:
            trace["code"] = code
        if ids:
            trace["ids"] = ids
        if counts:
            trace["counts"] = counts
        with self._lock:
            self._buffer.append(trace)
        return True

    def flush(self) -> int:
        with self._lock:
            items, self._buffer = self._buffer, []
        self.log.append(items)
        return len(items)

    def recent(
        self, since: float, kind: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self._lock:
            pending = list(self._buffer)
        items = [t for t in [*self.log.read(since=since), *pending] if t.get("t", 0) >= since]
        if kind:
            items = [t for t in items if t.get("kind") == kind]
        items.sort(key=lambda t: -float(t.get("t", 0)))
        return items[:limit]

    def get(self, trace_id: str, since: float) -> dict[str, Any] | None:
        for trace in self.recent(since, limit=100_000):
            if trace.get("id") == trace_id:
                return trace
        return None

    def where_time_goes(self, since: float) -> dict[str, Any]:
        """Per kind: the share of total time spent in each step, over the kept traces."""
        by_kind: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        totals: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for trace in self.recent(since, limit=100_000):
            kind = str(trace.get("kind"))
            counts[kind] += 1
            totals[kind] += float(trace.get("total_ms", 0))
            for span in trace.get("spans", []):
                by_kind[kind][str(span.get("name"))] += float(span.get("ms", 0))
        out: dict[str, Any] = {}
        for kind, steps in by_kind.items():
            total = totals[kind] or 1.0
            ordered = sorted(steps.items(), key=lambda item: -item[1])
            out[kind] = {
                "traces": counts[kind],
                "mean_ms": round(totals[kind] / counts[kind], 2) if counts[kind] else 0,
                "steps": [
                    {
                        "name": name,
                        "ms": round(ms, 2),
                        "share": round(ms / total, 4),
                        "mean_ms": round(ms / counts[kind], 2),
                    }
                    for name, ms in ordered
                ],
            }
        return out
