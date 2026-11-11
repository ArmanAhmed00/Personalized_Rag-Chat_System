"""The maintenance loop: runs shortly after start and then every hour.

* deletes metrics and traces older than ``log_days`` (and activity older than ``activity_days``,
  keeping the hash chain valid);
* deletes "Download everything" exports older than ``export_days``;
* deletes Trash items older than ``trash_days``;
* makes the scheduled backup (daily by default) and keeps the newest ``backup_keep``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from outlawz.backups import create_backup, last_scheduled, prune_scheduled
from outlawz.library.trash import purge_trash

if TYPE_CHECKING:
    from outlawz.state import AppState

log = logging.getLogger("outlawz")

FIRST_RUN_DELAY = 30.0
INTERVAL = 3600.0
DAY = 86_400


@dataclass
class MaintenanceReport:
    logs: int = 0
    exports: int = 0
    trash: int = 0
    backup: str | None = None
    backups_pruned: int = 0
    ran_at: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_once(state: AppState, *, now: float | None = None) -> MaintenanceReport:
    s = state.settings
    report = MaintenanceReport(ran_at=time.time())
    if state.obs is not None:
        state.obs.flush()
        report.logs = state.obs.prune(s.log_days, s.activity_days)
    report.exports = state.exports.expire(s.export_days)
    report.trash = purge_trash(state.data.trash_dir, s.trash_days, now=now)
    if s.backup_every == "daily":
        last = last_scheduled(state.data.backups_dir)
        if last is None or (now or time.time()) - last >= DAY:
            if state.documents is not None:
                state.documents.flush()
            path = create_backup(
                s.library,
                state.data.backups_dir,
                include_documents=s.backup_documents,
                prefix="outlawz-auto",
            )
            report.backup = path.name
            state.events.emit("backup_created", backup=path.name, scheduled=True)
    report.backups_pruned = prune_scheduled(state.data.backups_dir, s.backup_keep)
    state.last_maintenance = report.to_dict()
    return report


async def loop(state: AppState) -> None:
    await asyncio.sleep(FIRST_RUN_DELAY)
    while True:
        try:
            await asyncio.to_thread(run_once, state)
        except Exception as exc:  # keep the loop alive; the next hour tries again
            log.warning("Maintenance step failed: %s", type(exc).__name__)
        await asyncio.sleep(INTERVAL)
