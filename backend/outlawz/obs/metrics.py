"""Metrics: one line per request (the route *template*, never real ids; status; milliseconds) and
one per pipeline step. Lines are buffered in memory and appended to ``metrics-<day>.jsonl`` every
couple of seconds, so recording never slows a request down.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from outlawz.obs.daily import DailyLog

# Steps grouped on the Metrics page, in the order they're shown.
STEP_GROUPS = {
    "Chat": ["chat.first_word", "chat.answer", "chat.rewrite", "chat.retrieval"],
    "Search": ["search.total", "search.embed", "search.vector", "search.keyword", "search.rerank"],
    "Reading documents": [
        "ingest.document",
        "ingest.extract",
        "ingest.chunk",
        "ingest.embed",
        "ingest.write",
    ],
    "Analysis": [
        "analysis.ask",
        "analysis.plan",
        "analysis.compute",
        "analysis.summary",
        "analysis.run",
    ],
}
SKIP_ROUTES = frozenset({"/api/health"})


def percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "mean": None}
    array = np.asarray(values, dtype=float)
    p50, p95, p99 = np.percentile(array, [50, 95, 99])
    return {
        "p50": round(float(p50), 2),
        "p95": round(float(p95), 2),
        "p99": round(float(p99), 2),
        "mean": round(float(array.mean()), 2),
    }


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


class Metrics:
    def __init__(self, folder: Path) -> None:
        self.log = DailyLog(folder, "metrics")
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def request(self, method: str, route: str, status: int, ms: float) -> None:
        if route in SKIP_ROUTES:
            return
        self._add(
            {
                "kind": "request",
                "method": method,
                "route": route,
                "status": status,
                "ms": round(ms, 2),
            }
        )

    def step(self, name: str, ms: float, **numbers: Any) -> None:
        """A pipeline step. Extra values must be numbers, ids or short codes."""
        record: dict[str, Any] = {"kind": "step", "name": name, "ms": round(ms, 2)}
        for key, value in numbers.items():
            if _number(value) or (isinstance(value, str) and len(value) <= 40):
                record[key] = value
        self._add(record)

    def _add(self, record: dict[str, Any]) -> None:
        record["t"] = round(time.time(), 3)
        with self._lock:
            self._buffer.append(record)

    def flush(self) -> int:
        with self._lock:
            records, self._buffer = self._buffer, []
        self.log.append(records)
        return len(records)

    def records(self, since: float) -> list[dict[str, Any]]:
        with self._lock:
            pending = [r for r in self._buffer if r["t"] >= since]
        return [*self.log.read(since=since), *pending]

    def summary(self, window_s: int, buckets: int = 24) -> dict[str, Any]:
        now = time.time()
        since = now - window_s
        records = self.records(since)
        routes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        steps: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in records:
            if r.get("kind") == "request":
                routes[(str(r.get("method")), str(r.get("route")))].append(r)
            elif r.get("kind") == "step":
                steps[str(r.get("name"))].append(r)

        route_rows: list[dict[str, Any]] = []
        for (method, route), items in sorted(routes.items(), key=lambda kv: -len(kv[1])):
            times = [float(i["ms"]) for i in items if _number(i.get("ms"))]
            errors = sum(1 for i in items if int(i.get("status", 0)) >= 500)
            refused = sum(1 for i in items if 400 <= int(i.get("status", 0)) < 500)
            route_rows.append(
                {
                    "method": method,
                    "route": route,
                    "requests": len(items),
                    "errors": errors,
                    "refused": refused,
                    **percentiles(times),
                }
            )

        step_rows = {}
        for name, items in steps.items():
            entry: dict[str, Any] = {
                "count": len(items),
                **percentiles([float(i["ms"]) for i in items]),
            }
            for key in ("prompt_tokens", "completion_tokens", "passages", "pages"):
                values = [float(i[key]) for i in items if _number(i.get(key))]
                if values:
                    entry[key] = {"total": sum(values), "mean": round(sum(values) / len(values), 2)}
            outcomes: dict[str, int] = defaultdict(int)
            for i in items:
                if isinstance(i.get("outcome"), str):
                    outcomes[i["outcome"]] += 1
            if outcomes:
                entry["outcomes"] = dict(outcomes)
            step_rows[name] = entry

        size = window_s / buckets
        timeline = [
            {"t": round(since + k * size), "requests": 0, "errors": 0} for k in range(buckets)
        ]
        for r in records:
            if r.get("kind") != "request":
                continue
            k = min(buckets - 1, max(0, int((float(r["t"]) - since) // size)))
            timeline[k]["requests"] += 1
            if int(r.get("status", 0)) >= 500:
                timeline[k]["errors"] += 1

        total = sum(int(row["requests"]) for row in route_rows)
        all_times = [
            float(r["ms"]) for r in records if r.get("kind") == "request" and _number(r.get("ms"))
        ]
        return {
            "window_s": window_s,
            "requests": total,
            "errors": sum(int(row["errors"]) for row in route_rows),
            "latency": percentiles(all_times),
            "routes": route_rows,
            "steps": step_rows,
            "groups": STEP_GROUPS,
            "timeline": timeline,
        }
