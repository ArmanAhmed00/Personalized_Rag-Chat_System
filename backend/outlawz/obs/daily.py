"""Daily JSONL files: ``<prefix>-YYYY-MM-DD.jsonl`` in the app folder's ``logs``.

Records are numbers, ids and codes only (never document text, questions or answers). Writes are
append-only; a half-written last line from a crash is skipped on read. Old days are deleted by
:meth:`DailyLog.prune`.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from outlawz.store.files import append_jsonl_many, iter_jsonl

_NAME = re.compile(r"^(?P<prefix>[a-z]+)-(?P<day>\d{4}-\d{2}-\d{2})\.jsonl$")


def day_of(timestamp: float) -> date:
    return datetime.fromtimestamp(timestamp, UTC).date()


class DailyLog:
    def __init__(self, folder: Path, prefix: str) -> None:
        self.folder = folder
        self.prefix = prefix
        self._lock = threading.Lock()

    def path_for(self, day: date) -> Path:
        return self.folder / f"{self.prefix}-{day.isoformat()}.jsonl"

    def files(self) -> list[tuple[date, Path]]:
        found: list[tuple[date, Path]] = []
        if not self.folder.exists():
            return found
        for path in self.folder.iterdir():
            match = _NAME.match(path.name)
            if match and match.group("prefix") == self.prefix:
                found.append((date.fromisoformat(match.group("day")), path))
        return sorted(found)

    def append(self, records: list[dict[str, Any]], *, sync: bool = False) -> None:
        if not records:
            return
        by_day: dict[date, list[dict[str, Any]]] = {}
        for record in records:
            by_day.setdefault(day_of(float(record.get("t", time.time()))), []).append(record)
        with self._lock:
            for day, items in by_day.items():
                append_jsonl_many(self.path_for(day), items, sync=sync)

    def read(
        self, since: float | None = None, until: float | None = None
    ) -> Iterator[dict[str, Any]]:
        first = day_of(since) if since is not None else None
        last = day_of(until) if until is not None else None
        for day, path in self.files():
            if (first and day < first) or (last and day > last):
                continue
            for record in iter_jsonl(path):
                if not isinstance(record, dict):
                    continue
                t = record.get("t")
                if not isinstance(t, int | float):
                    continue
                if since is not None and t < since:
                    continue
                if until is not None and t > until:
                    continue
                yield record

    def prune(self, keep_days: int, today: date | None = None) -> list[Path]:
        """Delete files older than ``keep_days`` days. Returns the deleted paths."""
        cutoff = (today or datetime.now(UTC).date()) - timedelta(days=keep_days)
        removed = []
        with self._lock:
            for day, path in self.files():
                if day < cutoff:
                    try:
                        path.unlink()
                        removed.append(path)
                    except OSError:
                        continue
        return removed

    def size(self) -> int:
        return sum(path.stat().st_size for _, path in self.files() if path.exists())


WINDOWS = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600, "7d": 7 * 24 * 3600}


def window_seconds(window: str) -> int:
    from outlawz.errors import AppError

    if window not in WINDOWS:
        raise AppError("invalid_request", f"Choose a window: {', '.join(WINDOWS)}.", 422)
    return WINDOWS[window]
