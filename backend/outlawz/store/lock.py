"""One running copy per library.

The lock is a small JSON file in the app folder holding the owner's process id, computer name and
a heartbeat time that the owner refreshes every 30 seconds. A lock is *stale* (and is taken over)
when:

* it was written on this computer and that process is no longer running, or
* it was written on another computer and its heartbeat is more than 2 minutes old.

The second rule covers a library synced between computers (iCloud, OneDrive): the other computer
can't check our process list, but it can see that the heartbeat stopped.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from outlawz import __version__
from outlawz.errors import LibraryLocked
from outlawz.store.files import write_json

HEARTBEAT_SECONDS = 30.0
STALE_AFTER_SECONDS = 120.0
# A lock file that can't be parsed may be mid-write; only treat it as stale once it's this old.
UNREADABLE_GRACE_SECONDS = 5.0


def pid_alive(pid: int) -> bool:
    """Is a process with this id running on this computer?"""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:  # pragma: no cover - exercised on Windows CI
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


@dataclass
class LockInfo:
    pid: int
    host: str
    token: str
    started_at: float
    heartbeat_at: float
    version: str

    @classmethod
    def parse(cls, raw: Any) -> LockInfo | None:
        try:
            return cls(
                pid=int(raw["pid"]),
                host=str(raw["host"]),
                token=str(raw["token"]),
                started_at=float(raw["started_at"]),
                heartbeat_at=float(raw["heartbeat_at"]),
                version=str(raw.get("version", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _host() -> str:
    return socket.gethostname() or "this computer"


class InstanceLock:
    """Exclusive ownership of a library by one running copy of Outlawz."""

    def __init__(self, path: Path, *, stale_after: float = STALE_AFTER_SECONDS) -> None:
        self.path = path
        self.stale_after = stale_after
        self.token = uuid.uuid4().hex
        self.held = False

    def _info(self, now: float, started_at: float | None = None) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "host": _host(),
            "token": self.token,
            "started_at": started_at if started_at is not None else now,
            "heartbeat_at": now,
            "version": __version__,
        }

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            now = time.time()
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                existing = self._read()
                if self._is_stale(existing):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(self.path)
                    continue
                raise LibraryLocked(self._refusal(existing)) from None
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._info(now), fh)
                fh.flush()
                os.fsync(fh.fileno())
            self.held = True
            self._started_at = now
            return
        raise LibraryLocked("Couldn't take the library lock. Try starting Outlawz again.")

    def _read(self) -> LockInfo | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return LockInfo.parse(raw)

    def _is_stale(self, info: LockInfo | None) -> bool:
        if info is None:
            try:
                age = time.time() - self.path.stat().st_mtime
            except FileNotFoundError:
                return True
            return age > UNREADABLE_GRACE_SECONDS
        if info.host == _host():
            return not pid_alive(info.pid)
        return time.time() - info.heartbeat_at > self.stale_after

    def _refusal(self, info: LockInfo | None) -> str:
        if info is None:
            return (
                "Another copy of Outlawz is starting with this library right now. "
                "Wait a few seconds and try again."
            )
        since = datetime.fromtimestamp(info.started_at).strftime("%H:%M on %d %b")
        if info.host == _host():
            return (
                f"This library is already open in another copy of Outlawz "
                f"(process {info.pid}, opened at {since}). Close that copy first, "
                f"or use the one that's already running."
            )
        minutes = max(1, round(self.stale_after / 60))
        return (
            f"This library is open on another computer (“{info.host}”, since {since}). "
            f"Two computers must not open the same library at the same time. If Outlawz is "
            f"no longer running there, wait {minutes} minutes and try again."
        )

    def heartbeat(self) -> bool:
        """Refresh the heartbeat. Returns False if someone else now owns the lock."""
        if not self.held:
            return False
        info = self._read()
        if info is not None and info.token != self.token:
            self.held = False
            return False
        write_json(self.path, self._info(time.time(), self._started_at), indent=None)
        return True

    def release(self) -> None:
        if not self.held:
            return
        info = self._read()
        if info is None or info.token == self.token:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self.path)
        self.held = False

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
