"""The activity log: append-only and hash-chained with SHA-256.

Each event stores the hash of the event before it, and its own hash covers that link plus its
content. Changing, removing or reordering any event breaks the chain from that point on, and
"Check integrity" reports the first event that doesn't fit.

Events hold ids and short codes only (``document_deleted``, ``doc_id``), never document text,
questions or answers. Old daily files can be pruned: the last hash before the pruned days is kept
in ``activity-anchor.json`` so the remaining chain still checks out.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from outlawz.obs.daily import DailyLog
from outlawz.store.files import iter_jsonl, read_json, write_json

GENESIS = "0" * 64
ACTIONS = (
    "document_added",
    "document_deleted",
    "document_removed",
    "document_reread",
    "library_rebuilt",
    "samples_added",
    "chat_deleted",
    "preferences_changed",
    "settings_changed",
    "backup_created",
    "backup_verified",
    "backup_restored",
    "export_created",
)


def _canonical(event: dict[str, Any]) -> bytes:
    body = {k: event[k] for k in ("seq", "t", "action", "ids", "prev")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def event_hash(event: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(event)).hexdigest()


def _clean_ids(ids: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in ids.items():
        if value is None:
            continue
        if isinstance(value, bool | int | float):
            out[key] = value
        elif isinstance(value, str):
            out[key] = value[:80]
        elif isinstance(value, list | tuple):
            out[key] = [str(v)[:80] for v in value][:50]
    return out


class ActivityLog:
    def __init__(self, folder: Path) -> None:
        self.log = DailyLog(folder, "activity")
        self.anchor_file = folder / "activity-anchor.json"
        self._lock = threading.Lock()
        self._last: tuple[int, str] | None = None

    def _anchor(self) -> tuple[int, str]:
        raw = read_json(self.anchor_file, None)
        if (
            isinstance(raw, dict)
            and isinstance(raw.get("seq"), int)
            and isinstance(raw.get("hash"), str)
        ):
            return raw["seq"], raw["hash"]
        return 0, GENESIS

    def _tail(self) -> tuple[int, str]:
        if self._last is not None:
            return self._last
        last = self._anchor()
        for event in self._events():
            if isinstance(event.get("seq"), int) and isinstance(event.get("hash"), str):
                last = (event["seq"], event["hash"])
        self._last = last
        return last

    def _events(self) -> Iterator[dict[str, Any]]:
        for _, path in self.log.files():
            for event in iter_jsonl(path):
                if isinstance(event, dict):
                    yield event

    def record(self, action: str, **ids: Any) -> dict[str, Any]:
        with self._lock:
            seq, prev = self._tail()
            event: dict[str, Any] = {
                "seq": seq + 1,
                "t": round(time.time(), 3),
                "action": action,
                "ids": _clean_ids(ids),
                "prev": prev,
            }
            event["hash"] = event_hash(event)
            self.log.append([event], sync=True)
            self._last = (event["seq"], event["hash"])
            return event

    def check(self) -> dict[str, Any]:
        """Walk the chain. Reports the first changed or missing event, if any."""
        expected_seq, expected_prev = self._anchor()
        anchor_seq = expected_seq
        files = self.log.files()
        count = 0
        for _, path in files:
            lines = path.read_bytes().splitlines()
            for number, raw in enumerate(lines, start=1):
                if not raw.strip():
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    # A fragment left by a crash mid-append. It isn't trusted or counted; if it
                    # replaced a real event, the next event's link won't match and that's reported.
                    continue
                if not isinstance(event, dict) or not all(
                    k in event for k in ("seq", "t", "action", "ids", "prev", "hash")
                ):
                    return self._problem("unreadable", expected_seq + 1, path, number, count)
                if isinstance(event["seq"], int) and event["seq"] <= anchor_seq:
                    continue  # already covered by the anchor (a prune was interrupted)
                if event["seq"] != expected_seq + 1 or event["prev"] != expected_prev:
                    return self._problem("missing", expected_seq + 1, path, number, count)
                if event_hash(event) != event["hash"]:
                    return self._problem("changed", event["seq"], path, number, count)
                expected_seq, expected_prev = event["seq"], event["hash"]
                count += 1
        return {
            "ok": True,
            "events": count,
            "last_seq": expected_seq,
            "problem": None,
            "message": f"All {count:,} events check out.",
        }

    @staticmethod
    def _problem(kind: str, seq: int, path: Path, line: int, count: int) -> dict[str, Any]:
        words = {
            "changed": f"Event #{seq} was changed after it was written.",
            "missing": f"Event #{seq} is missing or out of order.",
            "unreadable": f"Event #{seq} can't be read.",
        }
        return {
            "ok": False,
            "events": count,
            "problem": {"kind": kind, "seq": seq, "file": path.name, "line": line},
            "message": words[kind] + f" Everything before it ({count:,} events) checks out.",
        }

    def query(
        self,
        *,
        action: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        items = [
            e
            for e in self.log.read(since=since, until=until)
            if action is None or e.get("action") == action
        ]
        items.sort(key=lambda e: -int(e.get("seq", 0)))
        return items[:limit]

    def to_csv(self, events: list[dict[str, Any]]) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["seq", "time_utc", "action", "ids", "hash"])
        for e in sorted(events, key=lambda e: int(e.get("seq", 0))):
            when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(e.get("t", 0))))
            ids = ";".join(f"{k}={v}" for k, v in sorted(e.get("ids", {}).items()))
            writer.writerow([e.get("seq"), when, e.get("action"), ids, e.get("hash")])
        return buf.getvalue()

    def prune(self, keep_days: int, today: date | None = None) -> int:
        """Delete old daily files, moving the anchor forward so the rest still checks out."""
        with self._lock:
            files = self.log.files()
            if not files:
                return 0
            from datetime import UTC, datetime, timedelta

            cutoff = (today or datetime.now(UTC).date()) - timedelta(days=keep_days)
            doomed = [(d, p) for d, p in files if d < cutoff and p != files[-1][1]]
            if not doomed:
                return 0
            anchor = self._anchor()
            for _, path in doomed:
                for event in iter_jsonl(path):
                    if isinstance(event, dict) and isinstance(event.get("seq"), int):
                        anchor = (event["seq"], str(event.get("hash")))
            write_json(self.anchor_file, {"seq": anchor[0], "hash": anchor[1]})
            for _, path in doomed:
                path.unlink(missing_ok=True)
            return len(doomed)
